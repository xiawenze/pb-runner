#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pb-runner: GitHub Actions 上的自续 Telegram bot 运行器

流程:
  1) 从 .git/config 取运行时 GITHUB_TOKEN (checkout 持久化凭据)
  2) 等位锁: 同时只有一个 bot 在轮询 (最多等 10 分钟)
  3) 从 draft release 拉私有 config.env + 状态资产
  4) 起 bot, 掉线重启, 每 5 分钟回传 runner.log/bot.log
  5) 结束存状态 + workflow_dispatch 自续
  全程把进度写进仓库 status.txt, 供外部实时查看
"""
import base64
import json
import os
import re
import subprocess
import sys
import time
import datetime

import requests

REPO = os.environ.get('GITHUB_REPOSITORY', '')
RUN_ID = os.environ.get('GITHUB_RUN_ID', '0')
RUN_MIN = int(os.environ.get('RUN_MINUTES', '330'))
API = f'https://api.github.com/repos/{REPO}'
UP = f'https://uploads.github.com/repos/{REPO}'
SESSION = requests.Session()


def log(*a):
    line = '[runner] ' + time.strftime('%H:%M:%S') + ' ' + ' '.join(str(x) for x in a)
    print(line, flush=True)
    try:
        with open('runner.log', 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return ''


# ---------------- token ----------------
def get_token():
    b64 = sh("git config --get-all http.https://github.com/.extraheader | awk '{print $NF}' | head -1")
    if b64:
        try:
            dec = base64.b64decode(b64).decode()
            if dec.startswith('x-access-token:'):
                return dec[len('x-access-token:'):]
        except Exception:
            pass
    for url in sh("git config --get-regexp '^remote\\..*\\.url$' | awk '{print $2}'").splitlines():
        m = re.match(r'https://[^:]+:([^@]+)@', url)
        if m:
            return m.group(1)
    try:
        with open(os.path.expanduser('~/.git-credentials')) as f:
            m = re.match(r'https://[^:]+:([^@]+)@', f.readline())
            if m:
                return m.group(1)
    except Exception:
        pass
    return ''


TOKEN = get_token()
if not TOKEN:
    log('!! 拿不到 GITHUB_TOKEN')
    sys.exit(1)
SESSION.headers.update({'Authorization': 'Bearer ' + TOKEN,
                        'Accept': 'application/vnd.github+json'})


def report(msg):
    """把进度写进仓库 status.txt（外部可见）"""
    try:
        body = (msg + '\n' + datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
                + f'\nrun={RUN_ID}')
        r = SESSION.get(API + '/contents/status.txt', timeout=20)
        data = {'message': 'status ' + time.strftime('%H:%M'),
                'content': base64.b64encode(body.encode()).decode()}
        if r.status_code == 200:
            data['sha'] = r.json().get('sha', '')
        SESSION.put(API + '/contents/status.txt', json=data, timeout=25)
    except Exception as e:
        log('report fail:', e)


log(f'token ok ({len(TOKEN)} chars) repo={REPO} run={RUN_ID} min={RUN_MIN}')
report('STARTED')

# ---------------- 等位锁 ----------------
def wf_runs(status):
    try:
        r = SESSION.get(f'{API}/actions/workflows/main.yml/runs',
                        params={'status': status, 'per_page': 50}, timeout=25)
        return r.json().get('workflow_runs', []) if r.status_code == 200 else []
    except Exception:
        return []


def wait_turn(max_wait=600):
    t0 = time.time()
    while True:
        allr = wf_runs('in_progress') + wf_runs('queued')
        mine = next((r['created_at'] for r in allr if str(r['id']) == str(RUN_ID)), None)
        if not mine:
            return True
        earlier = [r for r in allr
                   if str(r['id']) != str(RUN_ID) and r.get('created_at', '') < mine]
        if not earlier:
            return True
        waited = int(time.time() - t0)
        log(f'等位中 (前面 {len(earlier)} 个 run, 已等 {waited}s)')
        report(f'WAIT 前面{len(earlier)}个 run, 已等{waited}s')
        if waited >= max_wait:
            log('等位超时，强制接棒')
            return True
        time.sleep(60)


wait_turn()

# ---------------- draft release ----------------
def draft_id():
    r = SESSION.get(API + '/releases', timeout=25)
    for rel in (r.json() if r.status_code == 200 else []):
        if rel.get('draft'):
            return rel['id']
    return None


REL_ID = draft_id()
if not REL_ID:
    log('!! 没有 draft release')
    report('FAIL 无 draft release')
    sys.exit(1)
log('draft release', REL_ID)


def assets():
    r = SESSION.get(f'{API}/releases/{REL_ID}/assets', params={'per_page': 100}, timeout=25)
    return {a['name']: a for a in (r.json() if r.status_code == 200 else [])}


def download(name, dest):
    a = assets().get(name)
    if not a:
        return False
    try:
        r = SESSION.get(a['url'], headers={'Accept': 'application/octet-stream'},
                        timeout=40, allow_redirects=True)
        if r.status_code == 200 and r.content:
            open(dest, 'wb').write(r.content)
            return True
    except Exception as e:
        log('download fail', name, e)
    return False


def upload(name, path):
    if not os.path.exists(path):
        return
    try:
        old = assets().get(name)
        if old:
            SESSION.delete(old['url'], timeout=25)
            time.sleep(1)
        with open(path, 'rb') as f:
            r = requests.post(f'{UP}/releases/{REL_ID}/assets', params={'name': name},
                              headers={'Authorization': 'Bearer ' + TOKEN,
                                       'Content-Type': 'application/octet-stream'},
                              data=f, timeout=60)
        log(f'上传 {name} -> {r.status_code}')
    except Exception as e:
        log('upload fail', name, e)


if not download('config.env', '.env'):
    log('!! 拉 config.env 失败')
    report('FAIL 拉 config.env 失败')
    sys.exit(1)
keys = [l for l in open('.env', encoding='utf-8', errors='ignore') if re.match(r'^[A-Z_]+=', l)]
log(f'.env {os.path.getsize(".env")}B / {len(keys)} 键')
if len(keys) < 2:
    log('!! .env 不像配置')
    report('FAIL .env 异常')
    sys.exit(1)
os.makedirs('data', exist_ok=True)
for name in ('state.json', 'subs.json', 'nodepool.json', 'settings.json'):
    if download(name, 'data/' + name):
        log('载入', name)

report('RUNNING bot 启动中')
log('启动 bot')

# ---------------- 跑 bot ----------------
bot = None


def start_bot():
    f = open('bot.log', 'ab')
    p = subprocess.Popen([sys.executable, '-u', 'bot.py'], stdout=f, stderr=subprocess.STDOUT)
    return p


bot = start_bot()
log('bot pid', bot.pid)
end = time.time() + RUN_MIN * 60
restarts = 0
last_sync = time.time()
while time.time() < end:
    time.sleep(30)
    if bot.poll() is not None:
        restarts += 1
        log(f'bot 退出 (code={bot.returncode})，第 {restarts} 次重启')
        bot = start_bot()
        if restarts >= 40:
            log('重启过多，收尾')
            break
    if time.time() - last_sync >= 300:
        tail = ''
        try:
            tail = open('bot.log', encoding='utf-8', errors='ignore').read()[-300:]
        except Exception:
            pass
        report(f'RUNNING pid={bot.pid} 重启={restarts} 剩余={int((end-time.time())/60)}min\n--- bot.log 尾 ---\n' + tail)
        upload('bot.log', 'bot.log')
        upload('runner.log', 'runner.log')
        last_sync = time.time()

try:
    bot.terminate()
except Exception:
    pass
time.sleep(3)
log('bot 已停, 重启', restarts)

for n in ('state', 'subs', 'nodepool', 'settings'):
    p = f'data/{n}.json'
    if os.path.exists(p):
        upload(n + '.json', p)
upload('bot.log', 'bot.log')
upload('runner.log', 'runner.log')

# ---------------- 自续 ----------------
try:
    r = SESSION.post(f'{API}/actions/workflows/main.yml/dispatches',
                     json={'ref': 'main'}, timeout=25)
    log('self-dispatch ->', r.status_code)
    report(f'DONE 本轮结束(重启{restarts}) dispatch={r.status_code}')
except Exception as e:
    log('dispatch fail', e)
    report('FAIL dispatch ' + str(e)[:120])
