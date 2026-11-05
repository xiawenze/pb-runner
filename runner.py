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
import threading
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


# ---------- 备好 mihomo（真测活要用）----------
MIHOMO_URL = ('https://github.com/MetaCubeX/mihomo/releases/download/'
              'v1.19.2/mihomo-linux-amd64-v1.19.2.gz')


def ensure_mihomo():
    import gzip
    import shutil
    bin_path = os.path.join(os.getcwd(), 'mihomo')
    if os.path.exists(bin_path) and os.access(bin_path, os.X_OK):
        return bin_path
    try:
        r = requests.get(MIHOMO_URL, timeout=120)
        if r.status_code != 200:
            log('mihomo 下载失败 HTTP', r.status_code)
            return ''
        with open('mihomo.gz', 'wb') as f:
            f.write(r.content)
        with gzip.open('mihomo.gz', 'rb') as fin, open(bin_path, 'wb') as fout:
            shutil.copyfileobj(fin, fout)
        os.chmod(bin_path, 0o755)
        log('mihomo 就绪', os.path.getsize(bin_path), 'B')
        return bin_path
    except Exception as e:
        log('mihomo 准备失败', e)
        return ''


MIHOMO = ensure_mihomo()
if MIHOMO:
    os.environ['MIHOMO_BIN'] = MIHOMO

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


def upload(name, path, allow_empty=False):
    if not os.path.exists(path):
        return
    # 空库保护：subs.json 内容为空对象时，禁止覆盖远端（防清库）
    if name == 'subs.json' and not allow_empty:
        try:
            if not json.load(open(path, encoding='utf-8')):
                log('!! 本地 subs.json 是空库，跳过上传（防清空远端）')
                return
        except Exception:
            pass
    try:
        old = assets().get(name)
        # 备份：覆盖 subs.json 前先把远端旧版存一份
        if name == 'subs.json' and old:
            try:
                rr = SESSION.get(old['url'], headers={'Accept': 'application/octet-stream'},
                                 timeout=30, allow_redirects=True)
                if rr.status_code == 200 and rr.content:
                    requests.post(f'{UP}/releases/{REL_ID}/assets',
                                  params={'name': 'subs-backup-%d.json' % int(time.time() % 100000)},
                                  headers={'Authorization': 'Bearer ' + TOKEN,
                                           'Content-Type': 'application/octet-stream'},
                                  data=rr.content, timeout=60)
                    log('已备份旧 subs.json (%dB)' % len(rr.content))
            except Exception:
                pass
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
# 关键校验：远端有库但没下载成功 → 重试 3 次，仍失败就退出（防空库覆盖）
if assets().get('subs.json') and not os.path.exists('data/subs.json'):
    for i in range(3):
        log('subs.json 下载失败，重试', i + 1)
        if download('subs.json', 'data/subs.json'):
            break
        time.sleep(5)
    if not os.path.exists('data/subs.json'):
        report('FAIL subs.json 无法下载，本 run 退出保护远端库')
        log('!! 退出：不能用空库起跑')
        sys.exit(1)

# ---------- 每日深度挖矿（距上次 >20h 才跑）----------
def maybe_harvest():
    try:
        import json as _j
        st = {}
        if os.path.exists('data/harvest_state.json'):
            try:
                st = _j.load(open('data/harvest_state.json', encoding='utf-8'))
            except Exception:
                st = {}
        if time.time() - float(st.get('last_run') or 0) < 20 * 3600:
            log('挖矿：上次 %s，跳过' % time.strftime('%m-%d %H:%M', time.localtime(st.get('last_run') or 0)))
            return
        import harvest_daily as HD
        log('挖矿：开始深度挖矿')
        summary = HD.run({'quake_key': os.environ.get('QUAKE_KEY', '')}, log=log)
        txt = HD.report_text(summary)
        log('挖矿：%s' % txt.splitlines()[2] if len(txt.splitlines()) > 2 else 'done')
        # 推给主人
        for uid in os.environ.get('TG_ALLOWED_USERS', '').split(','):
            uid = uid.strip()
            if not uid:
                continue
            try:
                requests.post('https://api.telegram.org/bot%s/sendMessage' % os.environ.get('TG_BOT_TOKEN', ''),
                              json={'chat_id': int(uid), 'text': txt[:3900], 'parse_mode': 'HTML',
                                    'disable_web_page_preview': True}, timeout=30)
            except Exception as e:
                log('挖矿：推送失败', e)
        upload('harvest_state.json', 'data/harvest_state.json')
    except Exception as e:
        log('挖矿异常:', e)


threading.Thread(target=maybe_harvest, daemon=True).start()

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
        # 状态文件也跟着日志一起回传，防 runner 中途死掉丢库
        for _f in ('state', 'subs', 'nodepool', 'settings'):
            upload(_f + '.json', 'data/' + _f + '.json')
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
        upload(n + '.json', p, allow_empty=True)
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
