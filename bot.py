#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
panghubot — FOFA/Quake 资产搜索 + 机场订阅管理 Bot
菜单布局与交互复刻 PANGHU bot:
  帮助菜单|排序设置  FOFA搜索|Quake搜索  屏蔽词表|路径对应
  收藏列表|短链管理  导出链接|更新所有  临期列表|订阅列表
运行: python3 bot.py  (配置读同目录 .env)
"""
import os
import re
import sys
import json
import time
import threading
import traceback

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from core import subman
from core import fofa
from core import quake
from core import shortlink
from core import turnscan
from core import gen
from core import harvest
from core import realtest
from core import upload
from core.alive import fastping
from core.parse import parse_subscription, stats, looks_like_links, dedup, validate_nodes
from core.fetch import extract_urls

# ---------------------------------------------------------------- .env
def load_env():
    p = os.path.join(HERE, '.env')
    if not os.path.exists(p):
        return
    for line in open(p, encoding='utf-8'):
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


load_env()

BOT_TOKEN = os.environ.get('TG_BOT_TOKEN', '').strip()
API_BASE = os.environ.get('TG_API_BASE', 'https://api.telegram.org').rstrip('/')
ALLOWED_USERS = {x.strip() for x in os.environ.get('TG_ALLOWED_USERS', '').split(',') if x.strip()}
FOFA_EMAIL = os.environ.get('FOFA_EMAIL', '').strip()
FOFA_PROXY_KEY = os.environ.get('FOFA_PROXY_KEY', '').strip()
FOFA_KEY = os.environ.get('FOFA_KEY', '').strip()
QUAKE_KEY = os.environ.get('QUAKE_KEY', '').strip()
FOFA_SIZE = int(os.environ.get('FOFA_SIZE', '100'))
QUAKE_SIZE = int(os.environ.get('QUAKE_SIZE', '20'))

DATA_DIR = os.path.join(HERE, 'data')
STATE_FILE = os.path.join(DATA_DIR, 'state.json')
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')
os.makedirs(DATA_DIR, exist_ok=True)

STORE = subman.Store()
SHORTS = shortlink.ShortStore()
WAITING = {}          # chat_id -> ('fofa'|'quake'|'word_add'|'alias_add'|'short_new', None)
LIST_SNAP = {}        # chat_id -> [url,...]  列表页索引快照
HARVEST = {}          # chat_id -> [url,...]  上次搜索发现的订阅
HARVEST_DUP = {}      # chat_id -> int  同机场被跳过的条数
HARVEST_DEAD = {}     # chat_id -> int  真测后被丢弃的死链数
SEARCHING = set()     # 忙标记
JOBS_LOCK = threading.Lock()


def load_settings():
    d = {'sort': 'expire', 'blockwords': [], 'aliases': {}}
    if os.path.exists(SETTINGS_FILE):
        try:
            d.update(json.load(open(SETTINGS_FILE, encoding='utf-8')))
        except Exception:
            pass
    return d


def save_settings(d):
    tmp = SETTINGS_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, SETTINGS_FILE)


SET = load_settings()

BOT_COMMANDS = [
    ('start', '主菜单 / 全部按钮'),
    ('fofa', 'FOFA 搜索（可带语句）'),
    ('quake', 'Quake 搜索（可带语句）'),
    ('list', '订阅列表'),
    ('patrol', '全量巡检（真测活 + 死链）'),
    ('clean', '死链清理'),
    ('update', '更新所有订阅的流量/节点'),
    ('export', '导出链接（全部/有额度/存活）'),
    ('id', '查看 chat_id / user_id'),
    ('help', '帮助'),
]

FOFA_PRESETS = [
    ('📡 订阅泄漏(带token) ⭐', 'body="/api/v1/client/subscribe?token="'),
    ('🛰 Sub-Store 后端', 'header="X-Powered-By: Sub-Store"'),
    ('🧭 V2Board 面板', 'body="/api/v1/guest/comm/config"'),
    ('🔮 MiSub 中台', 'body="/api/public/profiles"'),
    ('📮 订阅落地页', 'body="订阅" && body="clash"'),
    ('🛰 TURN/STUN', 'protocol="stun"'),
    ('🔑 弱口令后台', 'body="password" && title="登录"'),
    ('🌐 机场官网', 'title="机场" && body="订阅"'),
]

QUAKE_PRESETS = [
    ('📡 订阅泄漏(带token) ⭐', 'body:"/api/v1/client/subscribe?token="'),
    ('🧭 V2Board 面板', 'app:"v2board"'),
    ('🌐 Sub-Store 后端', 'body:"Sub-Store"'),
    ('🛰 TURN/STUN', 'service:"stun"'),
    ('🔑 后台登录页', 'title:"登录" AND body:"password"'),
]

HELP = (
    '🛰 PANGHU 订阅管家\n'
    '资产搜索 · 订阅管理 · 节点转换\n'
    '━━━━━━━━━━━━━━\n\n'
    '▍搜索\n'
    '🔍 FOFA 搜索 — 发语句，结果导出 txt，顺手收割订阅\n'
    '🌐 Quake 搜索 — 360 鹰图视角，资产更全\n'
    '🛰 TURN 扫描 — TCPing → 协议识别 → 弱口令 → 中继验证\n\n'
    '▍订阅\n'
    '📋 订阅列表 — 全部订阅，点进去刷新/测活/收藏/删除\n'
    '⏳ 临期列表 — 按到期排序，自动标剩几天\n'
    '⭐ 收藏列表 — 只留常看的\n'
    '🔄 更新所有 — 全库刷新流量/节点数/到期\n'
    '🩺 全量巡检 — 逐条真测活 + 死链标记 + 临期提醒\n'
    '🧹 死链清理 — 一键删掉拉不动/全死的\n'
    '📤 导出链接 — 全部/收藏/有额度/存活 四种口径\n\n'
    '▍工具\n'
    '🧩 节点转换 — 任意节点/订阅/配置 → Clash/V2Ray/Sing-box/Surge/QX/Loon\n'
    '✂️ 短链管理 — is.gd / tinyurl\n'
    '🚫 屏蔽词表 — 命中词的行/节点不展示\n'
    '🔗 路径对应 — 名字 ↔ URL，聊天框打名字即回链\n'
    '🔀 排序设置 — 到期/流量/节点数/加入时间\n\n'
    '▍直接发消息\n'
    '• 订阅链接（可多条）→ 自动入库 + 识别机场名 + 查流量\n'
    '• /fofa <语句>、/quake <语句>\n'
    '• 发别名 → 回对应 URL\n'
    '• 发节点/配置 → 直接转换\n\n'
    '⚙️ TG_BOT_TOKEN / TG_ALLOWED_USERS / FOFA_PROXY_KEY / QUAKE_KEY'
)

# ---------------------------------------------------------------- TG API
def api(method, payload=None, files=None, timeout=70):
    url = f'{API_BASE}/bot{BOT_TOKEN}/{method}'
    for _ in range(3):
        try:
            if files:
                r = requests.post(url, data=payload or {}, files=files, timeout=timeout)
            else:
                r = requests.post(url, json=payload or {}, timeout=timeout)
            j = r.json()
            if j.get('ok'):
                return j.get('result')
            desc = j.get('description', '')
            if 'retry after' in desc.lower():
                time.sleep(5)
                continue
            print(f'[api] {method} fail: {desc}', flush=True)
            return None
        except Exception as e:
            print(f'[api] {method} err: {e}', flush=True)
            time.sleep(3)
    return None


def send(chat_id, text, keyboard=None, reply_to=None):
    payload = {'chat_id': chat_id, 'text': text[:4000]}
    if reply_to:
        payload['reply_to_message_id'] = reply_to
    if keyboard:
        payload['reply_markup'] = {'inline_keyboard': keyboard}
    return api('sendMessage', payload)


def edit(chat_id, msg_id, text, keyboard=None):
    payload = {'chat_id': chat_id, 'message_id': msg_id, 'text': text[:4000]}
    if keyboard:
        payload['reply_markup'] = {'inline_keyboard': keyboard}
    return api('editMessageText', payload)


def send_doc(chat_id, filename, content, caption='', reply_to=None):
    if isinstance(content, str):
        content = content.encode()
    files = {'document': (filename, content, 'text/plain')}
    payload = {'chat_id': chat_id, 'caption': caption[:1000]}
    if reply_to:
        payload['reply_to_message_id'] = reply_to
    return api('sendDocument', payload, files=files)


def btn(text, data):
    return {'text': text, 'callback_data': data}


# ---------------------------------------------------------------- 菜单
def _toggle(label, key):
    on = bool(SET.get(key, False))
    return btn(('✅ ' if on else '⬜ ') + label, 'cfg|' + key)


def _mode_hint():
    t = '全部入库' if not SET.get('filter_tool') and not SET.get('verify_harvest') else '有筛选'
    return t + '（点按钮可切换）'


def main_menu():
    return [
        [btn('❓ 帮助菜单', 'h|help'), btn('🔀 排序设置', 'c|menu')],
        [btn('🔍 FOFA 搜索', 's|fofa'), btn('🌐 Quake 搜索', 's|quake')],
        [btn('🚫 屏蔽词表', 'w|list'), btn('🔗 路径对应', 'a|list')],
        [btn('⭐ 收藏列表', 'f|list'), btn('✂️ 短链管理', 'k|menu')],
        [btn('📤 导出链接', 'e|menu'), btn('🔄 更新所有', 'u|all')],
        [btn('⏳ 临期列表', 'p|list'), btn('📋 订阅列表', 'l|list|0')],
        [btn('🛰 TURN 扫描', 'ts|menu'), btn('🧩 节点转换', 'cv|menu')],
        [btn('🩺 全量巡检', 'pt|run'), btn('🧹 死链清理', 'pt|clean')],
    ]


SORT_LABELS = {
    'expire': '到期时间 ↑', 'rest': '剩余流量 ↓',
    'nodes': '节点数 ↓', 'added': '加入时间 ↓',
}


def sort_menu():
    cur = SET.get('sort', 'expire')
    return [[btn(('✅ ' if k == cur else '') + v, f'c|{k}')]
            for k, v in SORT_LABELS.items()] + [[btn('↩️ 返回', 'm|main')]]


def back_row():
    return [btn('↩️ 主菜单', 'm|main')]


def page_row(prefix, page, total_pages):
    row = []
    if page > 0:
        row.append(btn('⬅️ 上一页', f'{prefix}|{page - 1}'))
    row.append(btn(f'{page + 1}/{total_pages}', 'noop'))
    if page < total_pages - 1:
        row.append(btn('下一页 ➡️', f'{prefix}|{page + 1}'))
    return row

# ---------------------------------------------------------------- 渲染
def fmt_left(sec):
    if not sec:
        return '—'
    d = int(sec)
    if d < 0:
        return '已过期'
    if d >= 86400:
        return f'{d // 86400}天'
    if d >= 3600:
        return f'{d // 3600}时'
    return f'{d // 60}分'


def sub_line(u, v):
    last = v.get('last') or {}
    name = v.get('name') or subman.host_of(u)
    star = '⭐' if v.get('fav') else ''
    nodes = last.get('nodes')
    flag = last.get('flag') or ''
    parts = [f'{star}{flag}{name}'.replace('  ', ' ')] if flag else [f'{star}{name}']
    if nodes:
        parts.append(f'🧩{nodes}')
    if last.get('alive'):
        parts.append(f'✅{last["alive"]}')
    if last.get('rest_gb') is not None:
        parts.append(f'💧{last["rest_gb"]}G')
    elif last.get('total_gb'):
        parts.append('❓限流')
    if last.get('expire'):
        left = last['expire'] - time.time()
        parts.append(f'⏳{fmt_left(left)}')
    elif last:
        parts.append('♾ 永久')
    return ' · '.join(parts)


def list_view(chat_id, kind='l', page=0, sub=None):
    """kind: l=订阅 p=临期 f=收藏; sub 是子视图的 key'last'/'del' 等"""
    if kind == 'f':
        urls = STORE.sorted_urls(SET.get('sort', 'expire'), only_fav=True)
        title = '⭐ 收藏列表'
    elif kind == 'p':
        allu = STORE.sorted_urls('expire')
        urls = [u for u in allu if (STORE.get(u) or {}).get('last', {}).get('expire')]
        title = '⏳ 临期列表'
    else:
        urls = STORE.sorted_urls(SET.get('sort', 'expire'))
        title = '📋 订阅列表'

    if not urls:
        return f'{title}\n\n（空）', [[btn('➕ 添加订阅', 'l|add_hint')]] + [back_row()]

    PER = 8
    tp = max(1, (len(urls) + PER - 1) // PER)
    page = max(0, min(page, tp - 1))
    chunk = urls[page * PER:(page + 1) * PER]
    LIST_SNAP[chat_id] = urls

    kb, lines = [], []
    for i, u in enumerate(chunk):
        v = STORE.get(u) or {}
        idx = page * PER + i
        lines.append(f'{idx + 1}. {sub_line(u, v)}')
        kb.append([btn(sub_line(u, v)[:60] or u[:60], f'd|{kind}|{idx}')])
    head = f'{title} · 共 {len(urls)} 条 · 第 {page + 1}/{tp} 页'
    kb.append(page_row(f'{kind}|list', page, tp))
    kb.append(back_row())
    return head + '\n\n' + '\n'.join(lines) + '\n\n点下方按钮操作', kb


def detail_view(chat_id, kind, idx):
    urls = LIST_SNAP.get(chat_id) or []
    if idx >= len(urls):
        return '该条已失效，回列表刷新一下。', [[btn('↩️ 返回', f'{kind}|list|0')]]
    u = urls[idx]
    v = STORE.get(u) or {}
    last = v.get('last') or {}
    star = '⭐ ' if v.get('fav') else ''
    lines = [f'{star}{v.get("name") or subman.host_of(u)}',
             f'🔗 {u}']
    if last:
        lines.append('')
        if last.get('nodes'):
            lines.append(f'节点数: {last["nodes"]}')
        if last.get('total_gb') is not None:
            lines.append(f'流量: 已用 {last.get("used_gb")}G / 总 {last.get("total_gb")}G '
                         f'/ 剩 {last.get("rest_gb")}G')
        elif last.get('total_gb'):
            lines.append('流量: 订阅未返回 userinfo')
        if last.get('expire'):
            lines.append(f'到期: {last.get("expire_date")} ({fmt_left(last["expire"] - time.time())})')
        elif last.get('expire_date'):
            lines.append(f'到期: {last.get("expire_date")}')
        else:
            lines.append('到期: 永久/未知')
        if last.get('ts'):
            lines.append('更新: ' + time.strftime('%m-%d %H:%M', time.localtime(last['ts'])))
    else:
        lines.append('\n⏳ 还没刷新过，点「刷新」查流量与节点数')
    kb = [
        [btn('🔄 刷新', f'r|{kind}|{idx}'), btn('📡 测活', f't|{kind}|{idx}')],
        [btn('⭐ 取消收藏' if v.get('fav') else '☆ 收藏', f'v|{kind}|{idx}'),
         btn('✏️ 重命名', f'n|{kind}|{idx}')],
        [btn('🔗 转短链', f'k2|{kind}|{idx}'), btn('🗑 删除', f'x|{kind}|{idx}')],
        [btn('↩️ 返回列表', f'{kind}|list|0')],
    ]
    return '\n'.join(lines), kb

# ---------------------------------------------------------------- 搜索
def do_search(chat_id, engine, q, reply_to=None, auto_turn=False):
    """后台线程执行搜索, 完成后发文件 + 收割订阅"""
    key = (chat_id, engine)
    with JOBS_LOCK:
        if key in SEARCHING:
            send(chat_id, '⏳ 这个搜索还在跑，等结果。')
            return
        SEARCHING.add(key)

    def _run():
        try:
            if engine == 'fofa':
                if not ((FOFA_EMAIL and FOFA_KEY) or FOFA_PROXY_KEY):
                    send(chat_id, '❌ 未配置 FOFA: 在 .env 里填 FOFA_EMAIL 和 FOFA_KEY')
                    return
                send(chat_id, f'🔍 FOFA 搜索中…\n查询: {q[:200]}')
                res = fofa.search(FOFA_EMAIL, FOFA_KEY, q, size=FOFA_SIZE)
            else:
                if not QUAKE_KEY:
                    send(chat_id, '❌ 未配置 Quake: 在 .env 里填 QUAKE_KEY')
                    return
                send(chat_id, f'🔍 QUAKE 搜索中…\n查询: {q[:200]}')
                res = quake.search(QUAKE_KEY, q, size=QUAKE_SIZE)

            rows = res['rows']
            TOOL_KW = ('订阅转换', '订阅工具', '转换工具', '替换工具', '测速', '中转', '工作台',
                       '同步', '镜像', '生成器', '订阅管理', '在线工具', 'SubReplay',
                       'Sub Sync', 'Sub-Store')
            tool_cnt = 0
            kept = []
            do_filter = bool(SET.get('filter_tool', False))
            for r0 in (rows if do_filter else []):
                ttl = (r0.get('title') or '') + ' ' + (r0.get('host') or '')
                if any(k.lower() in ttl.lower() for k in TOOL_KW):
                    tool_cnt += 1
                    continue
                kept.append(r0)
            if do_filter:
                rows = kept
            LAST_ROWS[chat_id] = rows
            if auto_turn:
                tg = turn_targets_from_rows(rows)
                if tg:
                    send(chat_id, f'🛰 搜索结果 {len(rows)} 条 → {len(tg)} 个目标, 开始 TURN 扫描…')
                    start_turn_scan(chat_id, tg)
            blockwords = SET.get('blockwords') or []
            lines = (fofa.render_rows(rows, blockwords) if engine == 'fofa'
                     else quake.render_rows(rows, blockwords))
            body = '\n'.join(lines) if lines else '(无结果)'

            # 收割订阅链接（平台不返回正文时，自己抓首页）
            harvested = []
            # —— 后端探测：Sub-Store /api/subs 与 MiSub，命中率远高于网页抓
            try:
                import concurrent.futures as _cf2
                hosts = [str(r0.get('host') or '') for r0 in rows[:16] if r0.get('host')]
                if hosts:
                    with _cf2.ThreadPoolExecutor(max_workers=8) as _ex2:
                        for got, note in _ex2.map(harvest.probe_host, hosts):
                            harvested.extend(got)
            except Exception as _e:
                print('[harvest] 后端探测失败', _e, flush=True)
            for r in rows:
                harvested.extend(subman.harvest_urls(r.get('body') or ''))
                harvested.extend(subman.harvest_urls(r.get('host') or ''))
            if len(set(harvested)) < 3 and rows:
                import concurrent.futures as _cf

                def _grab(r0):
                    host = str(r0.get('host') or '').strip()
                    port = str(r0.get('port') or '')
                    if not host:
                        return []
                    if not host.startswith(('http://', 'https://')):
                        scheme = 'https' if port in ('443', '8443') else 'http'
                        host = scheme + '://' + host
                        if port and port not in ('80', '443'):
                            host += ':' + port
                    try:
                        rr = requests.get(host, timeout=6, verify=False,
                                          headers={'User-Agent': 'clash-verge/v1.6.0'})
                        return subman.harvest_urls((rr.text or '')[:200000])
                    except Exception:
                        return []

                try:
                    with _cf.ThreadPoolExecutor(max_workers=8) as _ex:
                        for got in _ex.map(_grab, rows[:12]):
                            harvested.extend(got)
                except Exception:
                    pass
            seen, h2 = set(), []
            have_urls = set(STORE.d.keys())
            have_roots = set()
            for u0 in have_urls:
                r0 = subman.airport.root_label(u0)
                if r0:
                    have_roots.add(r0)
            seen_roots = set(SET.get('seen_roots') or [])
            have_roots |= seen_roots
            dup_same = 0
            for u in harvested:
                if u in seen or u in have_urls:
                    continue
                seen.add(u)
                root = subman.airport.root_label(u)
                if root and root in have_roots:
                    dup_same += 1      # 同机场，只是换了端口/子域
                    continue
                if root:
                    have_roots.add(root)
                h2.append(u)
            # —— 真测筛选：只留有活节点的（最多验 8 条，样本 4 节点/条）
            verified, dropped = [], 0
            if h2 and SET.get('verify_harvest', False):
                try:
                    import concurrent.futures as _cf3

                    def _fetch_nodes(u0):
                        try:
                            t0, _ui, _m = subman.fetch_sub(u0, timeout=12)
                            if not t0:
                                return u0, []
                            rr = parse_subscription(t0)
                            nn = rr[0] if isinstance(rr, tuple) else rr
                            return u0, nn[:4]
                        except Exception:
                            return u0, []

                    cand = h2[:8]
                    with _cf3.ThreadPoolExecutor(max_workers=6) as _ex3:
                        fetched = list(_ex3.map(_fetch_nodes, cand))
                    pool, owner = [], {}
                    for u0, nn in fetched:
                        if not nn:
                            dropped += 1
                            continue
                        for i, n0 in enumerate(nn):
                            n1 = dict(n0)
                            n1['name'] = '%d_%d' % (pool.__len__(), i)
                            owner[n1['name']] = u0
                            pool.append(n1)
                    if pool:
                        real2, info2 = realtest.test_nodes(pool, timeout_ms=3500, ready_wait=150)
                        alive_of = {}
                        for nm, d in (real2 or {}).items():
                            if d:
                                u0 = owner.get(nm)
                                if u0:
                                    alive_of[u0] = alive_of.get(u0, 0) + 1
                        for u0, _nn in fetched:
                            if alive_of.get(u0):
                                verified.append(u0)
                            elif u0 in [x for x, _ in fetched]:
                                dropped += 1
                except Exception as _e:
                    print('[harvest] 真测筛选失败', _e, flush=True)
                    verified = h2[:8]
            if verified:
                h2 = verified
            HARVEST_DUP[chat_id] = dup_same
            HARVEST[chat_id] = h2
            HARVEST_DEAD[chat_id] = dropped

            ts = time.strftime('%Y%m%d_%H%M%S')
            tag = 'fofa' if engine == 'fofa' else 'quake'
            fname = f'{tag}_{ts}_{len(lines)}条.txt'
            head = (f'{engine.upper()} 搜索完成\n'
                    f'查询: {q}\n'
                    f'总计: {res["total"]} · 本次导出: {len(lines)} 条\n'
                    f'domain: 无 · 屏蔽词过滤: {len(blockwords)} 个')
            print('[search] %s q=%s rows=%d total=%s harvest=%d'
                  % (engine, q[:60], len(lines), res.get('total'), len(h2)), flush=True)
            try:
                send_doc(chat_id, fname, head + '\n\n' + body,
                         caption=f'{engine.upper()} 完成 · {len(lines)}/{res["total"]} 条',
                         reply_to=reply_to)
            except Exception as de:
                print('[search] sendDoc 失败', de, flush=True)
                send(chat_id, '⚠️ 结果文件发送失败: %s' % str(de)[:120])

            kb = []
            if h2:
                kb.append([btn(f'📥 收割新机场 ({len(h2)})', 'hv|go')])
            kb.append([btn('↩️ 主菜单', 'm|main')])
            extra = ''
            if dup_same:
                extra = f'\n🔁 跳过 {dup_same} 条同机场（只是换了端口）'
            if tool_cnt:
                extra += '\n🧰 过滤工具站 %d 条（订阅转换/测速/中转）'
            extra = extra % tool_cnt if tool_cnt else extra
            dcount = HARVEST_DEAD.get(chat_id, 0)
            if dcount:
                extra += '\n🔬 真测淘汰死链 %d 条（只留实测有活节点的）' % dcount
            send(chat_id, f'{engine.upper()} 完成\n导出 {len(lines)} 条'
                          + (f'\n🆕 新机场 {len(h2)} 个' if h2 else '\n没有新机场')
                          + extra,
                 keyboard=kb)
        except Exception as e:
            msg = str(e)[:300]
            print('[search] FAIL %s %s' % (engine, msg), flush=True)
            send(chat_id, f'❌ {engine.upper()} 搜索失败:\n{msg}')
        finally:
            with JOBS_LOCK:
                SEARCHING.discard(key)

    threading.Thread(target=_run, daemon=True).start()


def harvest_add(chat_id):
    """把上次搜索收割到的链接批量入库 + 立即真测活"""
    urls = HARVEST.get(chat_id) or []
    if not urls:
        return '没有待收割的链接。', [[btn('↩️ 主菜单', 'm|main')]]
    added = 0
    roots = set(SET.get('seen_roots') or [])
    for u in urls:
        ok, _ = STORE.add(u)
        if ok:
            added += 1
            r = subman.airport.root_label(u)
            if r:
                roots.add(r)
    SET['seen_roots'] = sorted(roots)[-500:]
    save_settings(SET)
    HARVEST.pop(chat_id, None)
    dup = HARVEST_DUP.pop(chat_id, 0)

    def _bg(new_urls):
        msg = send(chat_id, f'🔬 入库 {len(new_urls)} 条，正在真测活…')
        mid = (msg or {}).get('message_id')
        try:
            res = realtest.verify_subs(new_urls, per_sub=4)
        except Exception as e:
            if mid:
                edit(chat_id, mid, f'❌ 验活异常: {str(e)[:120]}')
            return
        lines, alive_n = [], 0
        for u in new_urls:
            r = res.get(u) or {}
            nm = (STORE.get(u) or {}).get('name') or subman.host_of(u)
            a, n = r.get('alive', 0), r.get('nodes', 0)
            if a:
                alive_n += 1
                STORE.record(u, {'nodes': n, 'alive': a, 'best_ms': r.get('best'),
                                 'checked': int(time.time())})
                lines.append(f'✅ {nm[:22]} · {n}节点 · 活{a} · 最快{r.get("best")}ms')
            else:
                STORE.record(u, {'nodes': n, 'alive': 0, 'checked': int(time.time()),
                                 'err': r.get('err') or '无活节点'})
                lines.append(f'💀 {nm[:22]} · {n}节点 · 全死{r.get("err") and " (" + r["err"] + ")" or ""}')
        head = f'🔬 验活完成：{alive_n}/{len(new_urls)} 条有活节点'
        body = '\n'.join(lines[:10]) + ('\n…' if len(lines) > 10 else '')
        if mid:
            edit(chat_id, mid, (head + '\n\n' + body)[:3900],
                 [[btn('📋 订阅列表', 'l|list|0'), btn('🧹 死链清理', 'pt|clean')],
                  [btn('↩️ 主菜单', 'm|main')]])
        else:
            send(chat_id, (head + '\n\n' + body)[:3900])

    threading.Thread(target=_bg, args=([u for u in urls if u in STORE.d],), daemon=True).start()
    return (f'✅ 已入库 {added} 条新机场\n'
            f'🔁 跳过同机场旧端口 {dup} 条 · 已记 {len(roots)} 个机场\n'
            f'🔬 正在真测活，结果马上出…',
            [[btn('📋 订阅列表', 'l|list|0')], [btn('↩️ 主菜单', 'm|main')]])


# ---------------------------------------------------------------- 刷新/测活
def refresh_one(chat_id, url, msg_id=None, label=''):
    """拉订阅 → userinfo + 节点数; 有 msg_id 就原地改消息"""
    text, ui, meta = subman.fetch_sub(url)
    err = meta if (ui is None and text is None) else None
    brand = ''
    if isinstance(meta, dict):
        brand = meta.get('brand') or ''
    if err:
        payload = {'nodes': 0, 'rest_gb': None, 'total_gb': None,
                   'expire': 0, 'err': err, 'brand': brand}
        STORE.record(url, payload)
        out = f'❌ {label or subman.host_of(url)} 刷新失败: {err}'
    else:
        nodes = []
        try:
            r = parse_subscription(text)
            nodes = r[0] if isinstance(r, tuple) else r
        except Exception:
            pass
        payload = {
            'nodes': len(nodes),
            'rest_gb': ui.get('rest_gb') if ui else None,
            'total_gb': ui.get('total_gb') if ui else None,
            'used_gb': ui.get('used_gb') if ui else None,
            'expire': ui.get('expire') if ui else 0,
            'expire_date': ui.get('expire_date') if ui else None,
            'brand': brand or '',
            'flag': subman.airport.flag_from_url(url, text or ''),
        }
        STORE.record(url, payload)
        # 首次识别到品牌名 -> 自动把订阅名改成品牌名
        old_name = (STORE.get(url) or {}).get('name') or ''
        if brand and (not old_name or old_name == subman.host_of(url)):
            STORE.rename(url, brand)
        bits = [f'✅ {label or brand or subman.host_of(url)}']
        bits.append(f'{len(nodes)} 节点')
        if ui and ui.get('total_gb'):
            bits.append(f'剩 {ui["rest_gb"]}G / {ui["total_gb"]}G')
        if ui and ui.get('expire_date'):
            bits.append(f'到期 {ui["expire_date"]}')
        if not ui:
            bits.append('(无 userinfo)')
        out = ' · '.join(bits)
    if msg_id:
        edit(chat_id, msg_id, out, [[btn('↩️ 主菜单', 'm|main')]])
    return out, payload


def update_all(chat_id):
    urls = STORE.urls()
    if not urls:
        return '库是空的，先加订阅。', [[btn('↩️ 主菜单', 'm|main')]]
    msg = send(chat_id, f'🔄 更新所有… 0/{len(urls)}')
    mid = (msg or {}).get('message_id')

    def _run():
        ok = 0
        for i, u in enumerate(urls, 1):
            try:
                _, p = refresh_one(chat_id, u)
                if not p.get('err'):
                    ok += 1
            except Exception:
                pass
            if mid and (i % 3 == 0 or i == len(urls)):
                edit(chat_id, mid, f'🔄 更新所有… {i}/{len(urls)} (成功 {ok})')
        if mid:
            edit(chat_id, mid, f'✅ 更新完成: {ok}/{len(urls)} 成功',
                 [[btn('📋 看订阅列表', 'l|list|0')], [btn('↩️ 主菜单', 'm|main')]])

    threading.Thread(target=_run, daemon=True).start()
    return None, None


def _patrol_run(chat_id, mid, urls):
    """全量巡检: 逐条刷新 + 真测活, 汇总死链/临期"""
    dead, near, total_alive, report = [], [], 0, []
    for i, u in enumerate(urls, 1):
        v = STORE.get(u) or {}
        nm = v.get('name') or subman.host_of(u)
        try:
            text, ui, meta = subman.fetch_sub(u)
        except Exception as e:
            text, ui, meta = None, None, {'err': str(e)[:40]}
        if text is None:
            dead.append(nm)
            STORE.record(u, {'err': (meta or {}).get('err', 'err') if isinstance(meta, dict) else 'err',
                             'alive': 0, 'nodes': 0})
            report.append('❌ %s 拉取失败' % nm)
        else:
            nodes = []
            try:
                r = parse_subscription(text)
                nodes = r[0] if isinstance(r, tuple) else r
            except Exception:
                pass
            real, info = realtest.test_nodes(nodes, sample=25)
            a = sum(1 for x in real.values() if x) if real else 0
            best = min([x for x in real.values() if x], default=0)
            brand = (meta or {}).get('brand', '') if isinstance(meta, dict) else ''
            payload = {'nodes': len(nodes), 'alive': a, 'best_ms': best,
                       'rest_gb': (ui or {}).get('rest_gb'),
                       'total_gb': (ui or {}).get('total_gb'),
                       'expire': (ui or {}).get('expire') or 0,
                       'expire_date': (ui or {}).get('expire_date'),
                       'brand': brand,
                       'flag': subman.airport.flag_from_url(u, text)}
            STORE.record(u, payload)
            total_alive += a
            if a == 0:
                dead.append(nm)
                report.append('💀 %s 全死 (%d 节点)' % (nm, len(nodes)))
            else:
                report.append('✅ %s 活 %d/%d 最快 %dms' % (nm, a, len(real), best))
            exp = payload.get('expire') or 0
            if exp and 0 < exp - time.time() < 7 * 86400:
                near.append('%s 到期 %s' % (nm, payload.get('expire_date')))
        if mid and (i % 2 == 0 or i == len(urls)):
            try:
                edit(chat_id, mid, '🩺 巡检中 %d/%d … 活节点 %d' % (i, len(urls), total_alive))
            except Exception:
                pass

    head = '🩺 巡检完成 · %d 条订阅 · 活节点 %d' % (len(urls), total_alive)
    body = '\n'.join(report[:10]) + ('\n…' if len(report) > 10 else '')
    if near:
        body += '\n\n⏳ 临期提醒\n' + '\n'.join('· ' + x for x in near[:5])
    if dead:
        body += '\n\n💀 死链 %d 条，可点「🧹 死链清理」' % len(dead)
    if mid:
        try:
            edit(chat_id, mid, (head + '\n\n' + body)[:3900],
                 [[btn('🧹 死链清理', 'pt|clean')], [btn('📋 订阅列表', 'l|list|0')]])
        except Exception:
            pass


def test_alive(chat_id, url, msg_id=None, kind='l', idx=0):
    """测活: 拉节点 → TCP 快测, 播报进度"""
    text, ui, meta = subman.fetch_sub(url)
    err = meta if (text is None) else None
    if err:
        if msg_id:
            edit(chat_id, msg_id, f'❌ 拉取失败: {err}', [[btn('↩️ 返回', 'm|main')]])
        return
    nodes = []
    try:
        r = parse_subscription(text)
        nodes = r[0] if isinstance(r, tuple) else r
    except Exception:
        pass
    if not nodes:
        if msg_id:
            edit(chat_id, msg_id, '❌ 没解析出节点，无法测活', [[btn('↩️ 返回', 'm|main')]])
        return
    name = (STORE.get(url) or {}).get('name') or subman.host_of(url)
    mid = msg_id
    if mid:
        edit(chat_id, mid, f'📡 测活中 0/{len(nodes)}')

    def _progress(done, total):
        if mid and (done == total or done % max(1, total // 10) == 0):
            try:
                edit(chat_id, mid, f'📡 测活中 {done}/{total}')
            except Exception:
                pass

    def _run():
        try:
            real, info = realtest.test_nodes(nodes, sample=60, progress=_progress)
            if info.get('ok') and real:
                a = sum(1 for v in real.values() if v)
                best = min([v for v in real.values() if v], default=0)
                STORE.record(url, {'alive': a, 'dead': len(real) - a, 'nodes': len(nodes),
                                   'checked': int(time.time()), 'best_ms': best})
                out = (f'📡 {name} 真测活完成\n'
                       f'存活 {a} / {len(real)} (抽检)\n'
                       f'最快 {best}ms · 共 {len(nodes)} 节点\n'
                       f'判定方式: mihomo 真握手 gstatic/204')
            else:
                res = fastping(nodes, timeout=2.5, progress=_progress)
                a, d, un = len(res['alive']), len(res['dead']), len(res['unknown'])
                STORE.record(url, {'alive': a, 'dead': d, 'nodes': len(nodes),
                                   'checked': int(time.time())})
                why = info.get('err') or 'mihomo 不可用'
                out = (f'📡 {name} 测活完成 (降级 TCP)\n'
                       f'存活 {a} · 失联 {d} · UDP待定 {un} (共 {len(nodes)})\n'
                       f'降级原因: {str(why)[:60]}\n'
                       f'⚠️ TCP 判定有假阳性，仅供参考')
            if mid:
                edit(chat_id, mid, out, [[btn('🔄 重测', f't|{kind}|{idx}'), btn('↩️ 返回', 'm|main')]])
            else:
                send(chat_id, out, [[btn('↩️ 主菜单', 'm|main')]])
        except Exception as e:
            if mid:
                edit(chat_id, mid, f'❌ 测活异常: {str(e)[:150]}')

    threading.Thread(target=_run, daemon=True).start()

# ---------------------------------------------------------------- 导出
def export_menu():
    n = len(STORE.urls())
    fav = len([1 for v in STORE.d.values() if v.get('fav')])
    withquota = len([1 for v in STORE.d.values()
                     if (v.get('last') or {}).get('rest_gb') is not None])
    alive = len([1 for v in STORE.d.values() if (v.get('last') or {}).get('alive')])
    return (f'📤 导出链接 · 共 {n} 条\n'
            f'收藏 {fav} · 有额度 {withquota} · 存活 {alive}\n\n选口径:',
            [[btn(f'全部 ({n})', 'e|do|all'), btn(f'⭐ 收藏 ({fav})', 'e|do|fav')],
             [btn(f'有额度 ({withquota})', 'e|do|quota'), btn(f'存活 ({alive})', 'e|do|alive')],
             back_row()])


def do_export(chat_id, mode):
    urls = STORE.sorted_urls(SET.get('sort', 'expire'))
    if mode == 'fav':
        urls = [u for u in urls if (STORE.get(u) or {}).get('fav')]
    elif mode == 'quota':
        urls = [u for u in urls if (STORE.get(u) or {}).get('last', {}).get('rest_gb') is not None]
    elif mode == 'alive':
        urls = [u for u in urls if (STORE.get(u) or {}).get('last', {}).get('alive')]
    if not urls:
        return '❌ 这个口径下没有链接。'
    body = '\n'.join(urls)
    fname = f'export_{mode}_{time.strftime("%Y%m%d_%H%M%S")}.txt'
    send_doc(chat_id, fname, body, caption=f'导出 {len(urls)} 条 ({mode})')
    return f'✅ 已导出 {len(urls)} 条'


# ---------------------------------------------------------------- 屏蔽词 / 路径
def words_view():
    ws = SET.get('blockwords') or []
    body = '\n'.join(f'{i + 1}. {w}' for i, w in enumerate(ws)) or '（空）'
    kb = []
    for i, w in enumerate(ws[:10]):
        kb.append([btn(f'🗑 {w[:20]}', f'w|del|{i}')])
    kb.append([btn('➕ 添加屏蔽词', 'w|add'), btn('↩️ 主菜单', 'm|main')])
    return f'🚫 屏蔽词表 · {len(ws)} 个\n命中这些词的行/节点不展示\n\n{body}', kb


def aliases_view():
    al = SET.get('aliases') or {}
    lines = [f'{k} → {v[:60]}' for k, v in list(al.items())[:20]] or ['（空）']
    kb = []
    for k in list(al.keys())[:8]:
        kb.append([btn(f'🗑 {k[:24]}', f'a|del|{k}')])
    kb.append([btn('➕ 添加对应', 'a|add'), btn('↩️ 主菜单', 'm|main')])
    return ('🔗 路径对应 · 名字 ↔ URL\n'
            '在聊天框直接发名字即可回链接\n\n' + '\n'.join(lines)), kb


def shorts_view():
    d = SHORTS.d
    if not d:
        return '🔗 短链管理\n\n（空）\n在订阅详情点「转短链」生成。', [
            [btn('➕ 直接转换', 'k|new')], back_row()]
    lines = [f'{i + 1}. {x["short"]}\n   ← {x["long"][:70]}' for i, x in enumerate(d[-10:])]
    kb = [[btn(f'🗑 删 #{i + 1}', f'k|del|{len(d) - 10 + i}')] for i in range(max(0, len(d) - 10), len(d))]
    kb.append([btn('➕ 直接转换', 'k|new'), btn('↩️ 主菜单', 'm|main')])
    return f'🔗 短链管理 · {len(d)} 条 (最近10条)\n\n' + '\n'.join(lines), kb


# ---------------------------------------------------------------- TURN 扫描
TURN_PARAMS = {'c': 100, 'p': 500, 'pt': 3.0, 't': 5.0, 'ct': 8.0}
TURN_JOB = {}          # chat_id -> {'running','hits','targets','alive','total'}
LAST_ROWS = {}         # chat_id -> 上次 FOFA/Quake 的 rows
TURN_FOFA_Q = ('(app="Coturn-Server" || app="coturn-TURN-Server" || header="coturn" || '
               'banner="coturn" || port="3479" || port="3478" || port="5349" || '
               'server="Citrix" || server="Coturn" || title="TURN Server" || '
               'body_hash="-558802108" || body_hash="753498134" || protocol="stun" || '
               '"XOR-MAPPED-ADDRESS") && is_domain=false && tls.version="" && '
               'protocol!="unknown" && protocol!="ssh"')


def turn_menu(chat_id):
    p = TURN_PARAMS
    job = TURN_JOB.get(chat_id) or {}
    state = '⏳ 正在扫…' if job.get('running') else '空闲'
    text = ('🛰 TURN / STUN 扫描\n\n'
            '流程: TCPing 探活 → ALLOCATE 识别 → 24 组弱口令 → 中继验证\n'
            'TCP 中继: Permission+Connect+ConnectionBind → TLS 拿出口归属地\n'
            'UDP 中继: Allocate(UDP)+Permission+Send(DNS) → 校验回包\n\n'
            f'⚙️ 扫描并发 {p["c"]} · TCPing {p["p"]}\n'
            f'⏱ 超时 TCPing {p["pt"]}s · 扫描 {p["t"]}s · Connect {p["ct"]}s\n'
            f'📌 状态: {state}\n')
    if job.get('hits') or job.get('identified'):
        text += (f'🎯 上次: 可用中继 {len(job.get("hits") or [])} · '
                 f'仅识别 {len(job.get("identified") or [])} (目标 {job.get("total", 0)})\n')
    text += '\n点「粘贴 IP 列表」发 ip:port 清单, 或直接用上次 FOFA/Quake 的结果。'
    kb = []
    if job.get('hits'):
        kb.append([btn(f'📄 上次结果 ({len(job["hits"])})', 'ts|res')])
    kb.append([btn('🔍 FOFA 扫 TURN (内置语法)', 'ts|fofa')])
    kb.append([btn('📥 粘贴 IP 列表', 'ts|paste')])
    kb.append([btn('📋 用上次搜索结果', 'ts|last')])
    kb.append([btn('⚙️ 扫描参数', 'ts|cfg')])
    kb.append(back_row())
    return text, kb


def turn_res_view(chat_id):
    job = TURN_JOB.get(chat_id) or {}
    hits = job.get('hits') or []
    if not hits:
        return '还没有结果，先扫一轮。', [[btn('↩️ 返回', 'ts|menu')]]
    lines = [f'{i + 1}. {turnscan.fmt_line(h)}' for i, h in enumerate(hits[:40])]
    ident = job.get('identified') or []
    if ident:
        lines.append(f'\n— 仅识别(需认证) {len(ident)} 个 —')
        lines += [f'· {turnscan.fmt_line(h)}' for h in ident[:10]]
    head = (f'🎯 TURN 可用中继 {len(hits)} 个 · 仅识别 {len(ident)} 个\n'
            f'目标 {job.get("total", 0)} · TCP 存活 {job.get("alive", 0)}\n\n')
    kb = [[btn('📄 发文件', 'ts|doc'), btn('🔁 再扫一次', 'ts|rerun')],
          [btn('↩️ 返回', 'ts|menu')]]
    return head + '\n'.join(lines), kb


def turn_doc_body(chat_id):
    job = TURN_JOB.get(chat_id) or {}
    hits = job.get('hits') or []
    ident = job.get('identified') or []
    head = ('# TURN/STUN Scan\n'
            f'# 时间: {time.strftime("%Y-%m-%d %H:%M:%S")}\n'
            f'# 目标: {job.get("total", 0)}   TCP存活: {job.get("alive", 0)}   '
            f'可用中继: {len(hits)}   仅识别: {len(ident)}\n'
            '# 格式: ip:port  模式(ALL/TCP/UDP/AUTH)  出口归属地  '
            '认证(NO_AUTH/WEAK(账号:密码)/NEED_AUTH)  Software\n\n'
            '## 可用中继 (弱口令或免认证, 已过中继验证)\n')
    body = '\n'.join(turnscan.fmt_line(h) for h in hits) or '(无)'
    body += '\n\n## 仅识别 (TURN 服务在, 但要认证)\n'
    body += '\n'.join(turnscan.fmt_line(h) for h in ident) or '(无)'
    return head + body + '\n'


def start_turn_scan(chat_id, targets, msg_id=None):
    job = TURN_JOB.setdefault(chat_id, {})
    if job.get('running'):
        send(chat_id, '⏳ 上一轮还在扫，等它跑完再来。')
        return
    if not targets:
        send(chat_id, '❌ 没解析出有效目标。格式: `1.2.3.4:3478` 或 `1.2.3.4`(默认 3478)',
             [[btn('↩️ 返回', 'ts|menu')]])
        return
    if len(targets) > 20000:
        send(chat_id, f'❌ 目标太多 ({len(targets)}), 上限 20000, 分批来。')
        return

    p = dict(TURN_PARAMS)
    job.update({'running': True, 'hits': [], 'total': len(targets),
                'alive': 0, 'targets': list(targets)})

    if msg_id:
        mid = msg_id
        edit(chat_id, mid, f'🛰 TURN 扫描启动 · {len(targets)} 目标\nTCPing 阶段…')
    else:
        m = send(chat_id, f'🛰 TURN 扫描启动 · {len(targets)} 目标\nTCPing 阶段…')
        mid = (m or {}).get('message_id')

    last = [0.0]

    def prog(done, total, alive, hits):
        job['alive'] = alive
        now = time.time()
        if now - last[0] < 3:
            return
        last[0] = now
        if not mid:
            return
        try:
            if done < total:
                edit(chat_id, mid, f'🛰 TCPing 探活 {done}/{total}\n存活 {alive}')
            else:
                edit(chat_id, mid, f'🔬 协议识别 + 弱口令 + 中继验证\n'
                                   f'TCP 存活 {alive} · 已命中 {hits}')
        except Exception:
            pass

    def _run():
        try:
            res = turnscan.scan(targets, p['c'], p['p'], p['pt'], p['t'], p['ct'], prog)
        except Exception as e:
            job['running'] = False
            if mid:
                edit(chat_id, mid, f'❌ 扫描异常: {str(e)[:200]}',
                     [[btn('↩️ 返回', 'ts|menu')]])
            return
        hits = res['hits']
        ident = res.get('identified') or []
        job.update({'running': False, 'hits': hits, 'identified': ident,
                    'alive': res['alive']})
        summ = (f'✅ TURN 扫描完成\n'
                f'目标 {res["total"]} · TCP 存活 {res["alive"]}\n'
                f'可用中继 {len(hits)} · 仅识别(需认证) {len(ident)}\n'
                + ('\n'.join(turnscan.fmt_line(h) for h in hits[:10]) if hits else '\n(无可用中继)'))
        kb = [[btn('📄 发文件', 'ts|doc')], [btn('🔁 再扫一次', 'ts|rerun')],
              [btn('↩️ 返回', 'ts|menu')]]
        if mid:
            edit(chat_id, mid, summ[:4000], kb)
        else:
            send(chat_id, summ[:4000], kb)
        if hits:
            send_doc(chat_id, f'turn_results_{time.strftime("%Y%m%d_%H%M%S")}.txt',
                     turn_doc_body(chat_id), caption=f'TURN 命中 {len(hits)} 个')

    threading.Thread(target=_run, daemon=True).start()


def turn_targets_from_rows(rows):
    out = []
    for r in rows or []:
        host = str(r.get('host') or '').strip()
        port = str(r.get('port') or '').strip()
        if not host:
            continue
        if re.search(r':\d+$', host):      # host 自带端口, 别再拼
            out.append(host)
        elif port.isdigit():
            out.append(f'{host}:{port}')
        else:
            out.append(host)
    return turnscan.parse_targets('\n'.join(out))


# ---------------------------------------------------------------- 节点转换
NODES = {}                                  # chat_id -> 最近解析出的节点
POOL_FILE = os.path.join(DATA_DIR, 'nodepool.json')

FMTS = {
    'clash':   ('yaml', 'Clash / mihomo', gen.to_clash),
    'v2ray':   ('txt',  'V2Ray base64 订阅', gen.to_v2ray_b64),
    'singbox': ('json', 'Sing-box', gen.to_singbox),
    'surge':   ('conf', 'Surge', gen.to_surge),
    'qx':      ('conf', 'Quantumult X', gen.to_quantumultx),
    'loon':    ('conf', 'Loon', gen.to_loon),
    'links':   ('txt',  '纯节点链接', gen.to_links),
}


def load_pool():
    try:
        return json.load(open(POOL_FILE, encoding='utf-8'))
    except Exception:
        return []


def save_pool(lst):
    tmp = POOL_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(lst, f, ensure_ascii=False)
    os.replace(tmp, POOL_FILE)


def _fmt_stats(nodes, title):
    st = stats(nodes)
    types = ' · '.join(f'{k}×{v}' for k, v in sorted(st['types'].items(), key=lambda x: -x[1]))
    regions = ' · '.join(f'{k}×{v}' for k, v in sorted(st['regions'].items(), key=lambda x: -x[1])
                         if k != 'XX') or '—'
    return (f'{title}\n\n'
            f'✅ 节点 {st["total"]} 个\n'
            f'🔌 协议: {types or "—"}\n'
            f'🌍 地区: {regions}\n')


def nodes_kb(has_nodes=True):
    kb = []
    if has_nodes:
        kb.append([btn('Clash', 'cv|fmt|clash'), btn('V2Ray 订阅', 'cv|fmt|v2ray')])
        kb.append([btn('Sing-box', 'cv|fmt|singbox'), btn('Surge', 'cv|fmt|surge')])
        kb.append([btn('Quantumult X', 'cv|fmt|qx'), btn('Loon', 'cv|fmt|loon')])
        kb.append([btn('纯链接', 'cv|fmt|links'), btn('➕ 存入节点池', 'cv|save')])
    kb.append([btn('↩️ 节点转换', 'cv|menu')])
    return kb


def cv_menu(chat_id):
    pool = load_pool()
    text = ('🧩 节点转换 · 自定配置 → 订阅\n\n'
            '直接把下面任意一种发给我:\n'
            '• 节点链接 (ss:// vmess:// vless:// trojan:// hysteria2:// tuic:// …)\n'
            '• 一坨 base64 订阅内容\n'
            '• 整份 Clash / mihomo YAML\n'
            '• Sing-box JSON\n\n'
            '解析后可选格式: Clash / V2Ray / Sing-box / Surge / QX / Loon / 纯链接\n'
            f'📦 节点池: {len(pool)} 个节点\n')
    kb = [[btn('📥 粘贴节点 / 订阅 / 配置', 'cv|paste')],
          [btn(f'📦 节点池 ({len(pool)})', 'cv|pool')],
          [btn('📋 订阅列表', 'l|list|0')],
          back_row()]
    return text, kb


def handle_nodes_text(chat_id, text, msg_id=None):
    """任意节点文本 → 解析 → 展示可导出格式"""
    try:
        nodes, fmt = parse_subscription(text)
    except Exception as e:
        send(chat_id, f'❌ 解析失败: {str(e)[:150]}')
        return
    nodes = dedup(nodes)
    nodes, dropped = validate_nodes(nodes)
    if not nodes:
        send(chat_id, '❌ 没解析出可用节点。\n支持的: 节点链接 / base64 订阅 / Clash YAML / Sing-box JSON',
             [[btn('↩️ 节点转换', 'cv|menu')]])
        return
    NODES[chat_id] = nodes
    head = _fmt_stats(nodes, f'🧩 解析完成 · 来源 {fmt}')
    if dropped:
        head += '⚠️ 丢弃脏节点: ' + ', '.join(f'{k}×{v}' for k, v in dropped.items()) + '\n'
    head += '\n选一个格式导出，或先存进节点池。'
    if msg_id:
        edit(chat_id, msg_id, head, nodes_kb())
    else:
        send(chat_id, head, nodes_kb())


def do_convert(chat_id, fmt, nodes=None, tag='sub'):
    spec = FMTS.get(fmt)
    if not spec:
        return
    ext, label, fn = spec
    nodes = nodes if nodes is not None else (NODES.get(chat_id) or [])
    if not nodes:
        send(chat_id, '❌ 没有节点，先发点配置过来。', [[btn('↩️ 节点转换', 'cv|menu')]])
        return
    try:
        content = fn(nodes)
    except Exception as e:
        send(chat_id, f'❌ 生成 {label} 失败: {str(e)[:150]}')
        return
    fname = f'{tag}_{fmt}_{time.strftime("%Y%m%d_%H%M%S")}.{ext}'
    send_doc(chat_id, fname, content, caption=f'{label} · {len(nodes)} 节点')
    send(chat_id, f'✅ 已生成 {label} · {len(nodes)} 节点',
         [[btn('🔗 上传为订阅链接', f'cv|up|{fmt}|{tag}'), btn('↩️ 节点转换', 'cv|menu')]])


def pool_view(chat_id):
    pool = load_pool()
    if not pool:
        return ('📦 节点池 · 空\n\n解析节点后点「存入节点池」, 之后可一键导出整池订阅。',
                [[btn('📥 粘贴节点', 'cv|paste'), btn('↩️ 节点转换', 'cv|menu')]])
    nodes = pool
    head = _fmt_stats(nodes, '📦 节点池')
    kb = [[btn('Clash', 'cv|pfmt|clash'), btn('V2Ray 订阅', 'cv|pfmt|v2ray')],
          [btn('Sing-box', 'cv|pfmt|singbox'), btn('纯链接', 'cv|pfmt|links')],
          [btn('🧹 清空节点池', 'cv|pclear')],
          [btn('↩️ 节点转换', 'cv|menu')]]
    return head, kb


# ---------------------------------------------------------------- 回调
def handle_callback(cb):
    chat_id = cb['message']['chat']['id']
    mid = cb['message']['message_id']
    data = cb.get('data') or ''
    parts = data.split('|')
    cmd = parts[0]

    def show(text, kb=None):
        edit(chat_id, mid, text, kb)

    if cmd == 'noop':
        return

    if cmd == 'm' and parts[1] == 'main':
        edit(chat_id, mid, HELP, main_menu())
        return

    if cmd == 'h' and parts[1] == 'help':
        show(HELP, main_menu())
        return

    if cmd == 'cfg':                     # 开关
        k = parts[1]
        SET[k] = not bool(SET.get(k, False))
        save_settings(SET)
        label = '工具站过滤' if k == 'filter_tool' else '真测筛选'
        show('%s：%s\n当前：%s' % (label, '开' if SET[k] else '关', _mode_hint()),
             [[_toggle('🧰 工具站过滤', 'filter_tool'), _toggle('🔬 真测筛选', 'verify_harvest')],
              [btn('↩️ 返回', 'm|main')]])
        return

    if cmd == 'pt':                      # 巡检 / 死链清理
        if parts[1] == 'clean':
            dead = []
            for u in STORE.urls():
                v = STORE.get(u) or {}
                last = v.get('last') or {}
                if last.get('err') or (last.get('nodes') and last.get('alive') == 0):
                    dead.append(u)
            if not dead:
                show('🧹 没有发现死链', [[btn('↩️ 返回', 'm|main')]])
                return
            for u in dead:
                STORE.remove(u)
            show('🧹 已清理 %d 条死链\n\n' % len(dead)
                 + '\n'.join('· ' + d[:46] for d in dead[:8])
                 + ('\n…' if len(dead) > 8 else ''), main_menu())
            return
        # 全量巡检
        urls = STORE.urls()
        if not urls:
            show('库是空的', main_menu())
            return
        show('🩺 巡检开始 0/%d' % len(urls))
        threading.Thread(target=_patrol_run, args=(chat_id, mid, urls), daemon=True).start()
        return

    if cmd == 'c':                       # 排序
        if parts[1] == 'menu':
            show('🔀 排序设置 (影响列表与导出顺序)\n当前: '
                 + SORT_LABELS.get(SET.get('sort'), '到期时间 ↑'), sort_menu())
        else:
            SET['sort'] = parts[1]
            save_settings(SET)
            show('✅ 已设为: ' + SORT_LABELS.get(parts[1], parts[1]), sort_menu())
        return

    if cmd in ('l', 'p', 'f'):
        if parts[1] == 'add_hint':
            show('➕ 直接把订阅链接发给我即可入库\n(支持一次多条, 换行分隔)',
                 [[btn('↩️ 返回', f'{cmd}|list|0')]])
        else:
            page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
            t, kb = list_view(chat_id, cmd, page)
            show(t, kb)
        return

    if cmd == 'd':                       # 详情
        t, kb = detail_view(chat_id, parts[1], int(parts[2]))
        show(t, kb)
        return

    if cmd == 'r':                       # 刷新单条
        urls = LIST_SNAP.get(chat_id) or []
        idx = int(parts[2])
        if idx < len(urls):
            u = urls[idx]
            show('🔄 刷新中…')
            def _run():
                try:
                    out, _ = refresh_one(chat_id, u)
                    edit(chat_id, mid, out, [[btn('↩️ 返回', f'{parts[1]}|list|0')]])
                except Exception as e:
                    edit(chat_id, mid, f'❌ {str(e)[:150]}')
            threading.Thread(target=_run, daemon=True).start()
        return

    if cmd == 't':                       # 测活
        urls = LIST_SNAP.get(chat_id) or []
        idx = int(parts[2])
        if idx < len(urls):
            test_alive(chat_id, urls[idx], mid, parts[1], idx)
        return

    if cmd == 'v':                       # 收藏
        urls = LIST_SNAP.get(chat_id) or []
        idx = int(parts[2])
        if idx < len(urls):
            u = urls[idx]
            cur = bool((STORE.get(u) or {}).get('fav'))
            STORE.set_fav(u, not cur)
            t, kb = detail_view(chat_id, parts[1], idx)
            show(t, kb)
        return

    if cmd == 'n':                       # 重命名
        urls = LIST_SNAP.get(chat_id) or []
        idx = int(parts[2])
        if idx < len(urls):
            WAITING[chat_id] = ('rename', (parts[1], idx, urls[idx]))
            show('✏️ 发我新名字 (单行)', [[btn('↩️ 取消', f'{parts[1]}|list|0')]])
        return

    if cmd == 'x':                       # 删除
        urls = LIST_SNAP.get(chat_id) or []
        idx = int(parts[2])
        if idx < len(urls):
            show('🗑 确认删除？\n' + urls[idx][:120],
                 [[btn('✅ 确认删', f'x2|{parts[1]}|{idx}'), btn('↩️ 算了', f'{parts[1]}|list|0')]])
        return

    if cmd == 'x2':
        urls = LIST_SNAP.get(chat_id) or []
        idx = int(parts[2])
        if idx < len(urls):
            STORE.remove(urls[idx])
        t, kb = list_view(chat_id, parts[1], 0)
        show('✅ 已删除\n\n' + t, kb)
        return

    if cmd == 'k2':                      # 详情页转短链
        urls = LIST_SNAP.get(chat_id) or []
        idx = int(parts[2])
        if idx < len(urls):
            u = urls[idx]
            show('🔗 生成短链中…')
            def _run():
                s, err = shortlink.shorten(u)
                if s:
                    SHORTS.add(u, s)
                    edit(chat_id, mid, f'✅ 短链:\n{s}\n\n原链: {u[:80]}',
                         [[btn('↩️ 返回', f'{parts[1]}|list|0')]])
                else:
                    edit(chat_id, mid, f'❌ 生成失败: {err}',
                         [[btn('↩️ 返回', f'{parts[1]}|list|0')]])
            threading.Thread(target=_run, daemon=True).start()
        return

    if cmd == 'k':
        if parts[1] == 'menu':
            t, kb = shorts_view()
            show(t, kb)
        elif parts[1] == 'new':
            WAITING[chat_id] = ('short', None)
            show('🔗 发我要转短链的长链接', [[btn('↩️ 取消', 'k|menu')]])
        elif parts[1] == 'del':
            SHORTS.remove(int(parts[2]))
            t, kb = shorts_view()
            show(t, kb)
        return

    if cmd == 'u':
        r = update_all(chat_id)
        if r[0]:
            show(r[0], r[1])
        return

    if cmd == 'e':
        if parts[1] == 'menu':
            t, kb = export_menu()
            show(t, kb)
        elif parts[1] == 'do':
            show(do_export(chat_id, parts[2]))
        return

    if cmd == 'w':
        if parts[1] == 'list':
            t, kb = words_view()
            show(t, kb)
        elif parts[1] == 'add':
            WAITING[chat_id] = ('word', None)
            show('➕ 发我要屏蔽的词 (单行)', [[btn('↩️ 取消', 'w|list')]])
        elif parts[1] == 'del':
            ws = SET.get('blockwords') or []
            i = int(parts[2])
            if i < len(ws):
                ws.pop(i)
                SET['blockwords'] = ws
                save_settings(SET)
            t, kb = words_view()
            show(t, kb)
        return

    if cmd == 'a':
        if parts[1] == 'list':
            t, kb = aliases_view()
            show(t, kb)
        elif parts[1] == 'add':
            WAITING[chat_id] = ('alias', None)
            show('🔗 格式: 名字 空格 链接\n例: 老王的机场 https://xx/api/v1/client/subscribe?token=abc',
                 [[btn('↩️ 取消', 'a|list')]])
        elif parts[1] == 'del':
            al = SET.get('aliases') or {}
            al.pop(parts[2], None)
            SET['aliases'] = al
            save_settings(SET)
            t, kb = aliases_view()
            show(t, kb)
        return

    if cmd == 's':                       # 搜索入口
        eng = parts[1]
        presets = FOFA_PRESETS if eng == 'fofa' else QUAKE_PRESETS
        # 一键预设: s|fofa|3
        if len(parts) >= 3 and parts[2].isdigit():
            i = int(parts[2])
            if 0 <= i < len(presets):
                label, q = presets[i]
                show(f'🔍 {eng.upper()} 搜索中…\n{label}\n{q[:120]}')
                do_search(chat_id, eng, q)
            return
        # 预设菜单
        if eng == 'fofa':
            kb = [[btn(lb, f's|fofa|{i}')] for i, (lb, _q) in enumerate(presets)]
            kb.append([btn('✏️ 自定义语句', 's2|fofa'), btn('📊 上次收割', 'hv|go')])
            kb.append([_toggle('🧰 工具站过滤', 'filter_tool'), _toggle('🔬 真测筛选', 'verify_harvest')])
            kb.append([btn('↩️ 主菜单', 'm|main')])
            show('🔍 FOFA 一键搜索\n点一下直接出结果\n'
                 '（代理平台 100 次/天 · 30 次/分）\n'
                 '当前：' + _mode_hint(), kb)
            return
        kb = [[btn(lb, f's|quake|{i}')] for i, (lb, _q) in enumerate(presets)]
        kb.append([btn('✏️ 自定义语句', 's2|quake')])
        kb.append([_toggle('🧰 工具站过滤', 'filter_tool'), _toggle('🔬 真测筛选', 'verify_harvest')])
        kb.append([btn('↩️ 主菜单', 'm|main')])
        show('🌐 Quake 一键搜索\n点一下直接出结果\n当前：' + _mode_hint(), kb)
        return

    if cmd == 's2':                      # 自定义语句
        WAITING[chat_id] = ('fofa' if parts[1] == 'fofa' else 'quake', None)
        tip = ('示例:\nbody="/api/v1/client/subscribe?token="\n'
               'header="X-Powered-By: Sub-Store"' if parts[1] == 'fofa' else
               '示例:\napp:"v2board"\nbody:"subscription-userinfo"')
        show(f'✏️ 发我 {parts[1].upper()} 查询语句\n\n{tip}',
             [[btn('↩️ 取消', 'm|main')]])
        return

    if cmd == 'ts':
        sub = parts[1] if len(parts) > 1 else 'menu'
        job = TURN_JOB.get(chat_id) or {}
        if sub == 'menu':
            t, kb = turn_menu(chat_id)
            show(t, kb)
        elif sub == 'paste':
            WAITING[chat_id] = ('turn', None)
            show('📥 发我 IP 列表\n一行一个: `ip:port` 或 `ip`(默认 3478)\n'
                 '# 开头算注释, 自动去重\n\n也可以直接发 FOFA 导出的 txt 内容',
                 [[btn('↩️ 取消', 'ts|menu')]])
        elif sub == 'fofa':
            if not ((FOFA_EMAIL and FOFA_KEY) or FOFA_PROXY_KEY):
                show('❌ 未配置 FOFA: 在 .env 填 FOFA_EMAIL / FOFA_KEY\n\n'
                     '也可以自己粘贴 IP 列表扫。',
                     [[btn('📥 粘贴 IP 列表', 'ts|paste'), btn('↩️ 返回', 'ts|menu')]])
                return
            show('🔍 FOFA 扫 TURN 服务器中…\n(内置语法: coturn / stun / 3478-5349)',
                 [[btn('↩️ 返回', 'ts|menu')]])
            do_search(chat_id, 'fofa', TURN_FOFA_Q, auto_turn=True)
        elif sub == 'last':
            rows = LAST_ROWS.get(chat_id) or []
            if not rows:
                show('❌ 还没有搜索结果，先跑一次 FOFA/Quake 搜索。',
                     [[btn('↩️ 返回', 'ts|menu')]])
            else:
                tg = turn_targets_from_rows(rows)
                show(f'📋 上次搜索 {len(rows)} 条 → {len(tg)} 个目标\n开始扫描…',
                     [[btn('↩️ 返回', 'ts|menu')]])
                start_turn_scan(chat_id, tg, mid)
        elif sub == 'cfg':
            p = TURN_PARAMS
            WAITING[chat_id] = ('turncfg', None)
            show(f'⚙️ 扫描参数\n扫描并发 -c {p["c"]} · TCPing -p {p["p"]}\n'
                 f'超时 -pt {p["pt"]} / -t {p["t"]} / -ct {p["ct"]} (秒)\n\n'
                 '发我新参数, 例:\n`-c 200 -p 800 -pt 3 -t 6 -ct 10`',
                 [[btn('↩️ 返回', 'ts|menu')]])
        elif sub == 'res':
            t, kb = turn_res_view(chat_id)
            show(t, kb)
        elif sub == 'doc':
            if job.get('hits'):
                send_doc(chat_id, f'turn_results_{time.strftime("%Y%m%d_%H%M%S")}.txt',
                         turn_doc_body(chat_id), caption=f'TURN 命中 {len(job["hits"])} 个')
                show('📄 结果文件已发送', [[btn('↩️ 返回', 'ts|menu')]])
            else:
                show('没有结果文件，先扫一轮。', [[btn('↩️ 返回', 'ts|menu')]])
        elif sub == 'rerun':
            tg = job.get('targets') or []
            if tg:
                start_turn_scan(chat_id, tg, mid)
            else:
                show('没有上次的目标，重新粘贴列表。', [[btn('↩️ 返回', 'ts|menu')]])
        return

    if cmd == 'cv':
        sub = parts[1] if len(parts) > 1 else 'menu'
        if sub == 'menu':
            t, kb = cv_menu(chat_id)
            show(t, kb)
        elif sub == 'paste':
            WAITING[chat_id] = ('nodes', None)
            show('📥 把节点配置整段发我\n\n'
                 '• 节点链接 (ss/vmess/vless/trojan/hysteria2/tuic…)\n'
                 '• base64 订阅内容\n'
                 '• Clash / mihomo YAML\n'
                 '• Sing-box JSON',
                 [[btn('↩️ 取消', 'cv|menu')]])
        elif sub == 'fmt':
            show('⏳ 生成中…')
            threading.Thread(target=do_convert, args=(chat_id, parts[2]),
                             daemon=True).start()
        elif sub == 'save':
            nodes = NODES.get(chat_id) or []
            if not nodes:
                show('❌ 先发节点配置解析一次。', [[btn('↩️ 节点转换', 'cv|menu')]])
                return
            pool = load_pool()
            before = len(pool)
            merged, _ = validate_nodes(dedup(pool + nodes))
            save_pool(merged)
            show(f'✅ 已存入节点池\n本次 {len(nodes)} 个 · 池内 {len(merged)} 个'
                 f' (新增 {len(merged) - before})\n\n点「节点池」可整池导出订阅。',
                 [[btn('📦 看节点池', 'cv|pool'), btn('↩️ 节点转换', 'cv|menu')]])
        elif sub == 'pool':
            t, kb = pool_view(chat_id)
            show(t, kb)
        elif sub == 'pfmt':
            show('⏳ 整池生成中…')
            threading.Thread(target=do_convert,
                             args=(chat_id, parts[2], load_pool(), 'pool'),
                             daemon=True).start()
        elif sub == 'pclear':
            show('🧹 确认清空整个节点池？',
                 [[btn('✅ 确认清空', 'cv|pclear2'), btn('↩️ 算了', 'cv|pool')]])
        elif sub == 'pclear2':
            save_pool([])
            t, kb = pool_view(chat_id)
            show('✅ 已清空\n\n' + t, kb)
        elif sub == 'up':
            fmt = parts[2]
            tag = parts[3] if len(parts) > 3 else 'sub'
            nodes = load_pool() if tag == 'pool' else (NODES.get(chat_id) or [])
            spec = FMTS.get(fmt)
            if not spec or not nodes:
                show('❌ 没有可上传的内容。', [[btn('↩️ 节点转换', 'cv|menu')]])
                return
            ext, label, fn = spec
            show('☁️ 上传中…')
            mid2 = mid

            def _up():
                try:
                    content = fn(nodes)
                except Exception as e:
                    edit(chat_id, mid2, f'❌ 生成失败: {str(e)[:150]}')
                    return
                fname = f'{tag}_{fmt}_{time.strftime("%Y%m%d_%H%M%S")}.{ext}'
                url, err = upload.upload_text(fname, content)
                if url:
                    edit(chat_id, mid2,
                         f'🔗 {label} 订阅直链\n\n{url}\n\n'
                         f'{len(nodes)} 节点 · 直接粘进客户端订阅栏',
                         [[btn('↩️ 节点转换', 'cv|menu')]])
                else:
                    edit(chat_id, mid2, f'❌ 上传失败: {str(err)[:200]}',
                         [[btn('↩️ 节点转换', 'cv|menu')]])
            threading.Thread(target=_up, daemon=True).start()
        return

    if cmd == 'hv':
        t, kb = harvest_add(chat_id)
        show(t, kb)
        return

# ---------------------------------------------------------------- 消息
def handle_message(m):
    chat_id = m['chat']['id']
    uid = str((m.get('from') or {}).get('id', ''))
    if ALLOWED_USERS and uid not in ALLOWED_USERS:
        send(chat_id, f'⛔ 不在白名单。你的 id: {uid}')
        return
    text = (m.get('text') or '').strip()
    if not text:
        return

    # 命令
    if text.startswith('/'):
        cmd = text.split()[0].split('@')[0].lower()
        args = text[len(text.split()[0]):].strip()
        if cmd in ('/start', '/menu'):
            send(chat_id, '👋 点下方按钮操作\n\n' + HELP, main_menu())
        elif cmd == '/help':
            send(chat_id, HELP, main_menu())
        elif cmd in ('/fofa', '/quake'):
            engine = cmd[1:]
            if args:
                do_search(chat_id, engine, args)
            else:
                WAITING[chat_id] = (engine, None)
                send(chat_id, f'🔍 发我 {engine.upper()} 查询语句',
                     [[btn('↩️ 取消', 'm|main')]])
        elif cmd == '/id':
            send(chat_id, f'chat_id: {chat_id}\nuser_id: {uid}')
        elif cmd in ('/list', '/subs'):
            t, kb = list_view(chat_id, 'l', 0)
            send(chat_id, t, kb)
        elif cmd == '/patrol':
            urls = STORE.urls()
            if not urls:
                send(chat_id, '库是空的，先发订阅链接。', main_menu())
            else:
                msg = send(chat_id, '🩺 巡检开始 0/%d' % len(urls))
                threading.Thread(target=_patrol_run, args=(chat_id, (msg or {}).get('message_id'), urls),
                                 daemon=True).start()
        elif cmd == '/clean':
            dead = [u for u in STORE.urls()
                    if (STORE.get(u) or {}).get('last', {}).get('err')
                    or ((STORE.get(u) or {}).get('last', {}).get('nodes')
                        and (STORE.get(u) or {}).get('last', {}).get('alive') == 0)]
            if not dead:
                send(chat_id, '🧹 没有死链', main_menu())
            else:
                for u in dead:
                    STORE.remove(u)
                send(chat_id, '🧹 已清理 %d 条死链' % len(dead), main_menu())
        elif cmd == '/update':
            t, kb = update_all(chat_id)
            send(chat_id, t, kb)
        elif cmd == '/export':
            t, kb = export_menu()
            send(chat_id, t, kb)
        else:
            send(chat_id, '不认识这个命令，点 /start 看菜单。', main_menu())
        return

    # 等待输入
    st = WAITING.get(chat_id)
    if st:
        kind, extra = st
        WAITING.pop(chat_id, None)
        if kind in ('fofa', 'quake'):
            do_search(chat_id, kind, text)
            return
        if kind == 'turn':
            tg = turnscan.parse_targets(text)
            if not tg:
                send(chat_id, '❌ 没解析出目标。格式: `1.2.3.4:3478` 或 `1.2.3.4`',
                     [[btn('↩️ 返回', 'ts|menu')]])
                return
            start_turn_scan(chat_id, tg)
            return
        if kind == 'turncfg':
            for k in ('c', 'p'):
                mm = re.search(rf'-{k}\s+(\d+)', text)
                if mm:
                    TURN_PARAMS[k] = int(mm.group(1))
            for k in ('pt', 't', 'ct'):
                mm = re.search(rf'-{k}\s+([\d.]+)', text)
                if mm:
                    TURN_PARAMS[k] = float(mm.group(1))
            p = TURN_PARAMS
            send(chat_id, f'✅ 参数已更新\n并发 {p["c"]} · TCPing {p["p"]}\n'
                          f'超时 {p["pt"]}/{p["t"]}/{p["ct"]}s',
                 [[btn('🛰 回 TURN 扫描', 'ts|menu')]])
            return
        if kind == 'nodes':
            handle_nodes_text(chat_id, text)
            return
        if kind == 'word':
            ws = SET.get('blockwords') or []
            for w in re.split(r'[,，\s]+', text):
                if w and w not in ws:
                    ws.append(w)
            SET['blockwords'] = ws
            save_settings(SET)
            t, kb = words_view()
            send(chat_id, '✅ 已添加\n\n' + t, kb)
            return
        if kind == 'alias':
            m2 = re.match(r'(\S+)\s+(https?://\S+)', text)
            if not m2:
                send(chat_id, '❌ 格式不对，要: 名字 空格 链接', [[btn('↩️ 返回', 'a|list')]])
                return
            al = SET.get('aliases') or {}
            al[m2.group(1)] = m2.group(2)
            SET['aliases'] = al
            save_settings(SET)
            # 若该 URL 已在库, 顺手改名
            if m2.group(2) in STORE.d:
                STORE.rename(m2.group(2), m2.group(1))
            t, kb = aliases_view()
            send(chat_id, '✅ 已添加\n\n' + t, kb)
            return
        if kind == 'short':
            s, err = shortlink.shorten(text)
            if s:
                SHORTS.add(text, s)
                send(chat_id, f'✅ 短链:\n{s}', [[btn('🔗 短链列表', 'k|menu')]])
            else:
                send(chat_id, f'❌ 生成失败: {err}', [[btn('↩️ 返回', 'k|menu')]])
            return
        if kind == 'rename':
            kind2, idx, url = extra
            name = text.splitlines()[0][:48]
            STORE.rename(url, name)
            t, kb = detail_view(chat_id, kind2, idx)
            send(chat_id, '✅ 已重命名\n\n' + t, kb)
            return
        return

    # 别名回链
    al = SET.get('aliases') or {}
    if text in al:
        send(chat_id, f'🔗 {text}\n{al[text]}')
        return

    # 裸链接 → 入库
    urls = extract_urls(text) or ([text] if re.match(r'https?://\S+$', text) else [])
    urls = [u for u in urls if re.match(r'https?://', u)]
    if urls:
        added, dup = [], []
        for u in urls:
            ok, why = STORE.add(u)
            (added if ok else dup).append(u)
        if not added:
            send(chat_id, f'ℹ️ 这些已经在库里了 ({len(dup)} 条)',
                 [[btn('📋 订阅列表', 'l|list|0')]])
            return
        msg = send(chat_id, f'➕ 入库 {len(added)} 条，正在查流量…')
        mid = (msg or {}).get('message_id')

        def _run():
            lines = []
            for i, u in enumerate(added, 1):
                try:
                    out, _ = refresh_one(chat_id, u)
                    lines.append(out)
                except Exception as e:
                    lines.append(f'❌ {u[:40]}: {str(e)[:60]}')
                if mid:
                    edit(chat_id, mid, f'➕ 入库 {len(added)} 条 · 查询 {i}/{len(added)}')
            txt = '\n'.join(lines)
            if mid:
                edit(chat_id, mid, txt[:3800], [[btn('📋 订阅列表', 'l|list|0')],
                                                [btn('↩️ 主菜单', 'm|main')]])
            else:
                send(chat_id, txt[:3800])
        threading.Thread(target=_run, daemon=True).start()
        return

    # 节点配置 → 订阅转换 (裸节点链接 / Clash YAML / Sing-box JSON)
    if (looks_like_links(text) or 'proxies:' in text[:4000]
            or '"outbounds"' in text[:4000]):
        handle_nodes_text(chat_id, text)
        return

    send(chat_id, '没看懂。发订阅链接 / 点菜单按钮 / 或 /help', main_menu())


# ---------------------------------------------------------------- 主循环
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE, encoding='utf-8'))
        except Exception:
            pass
    return {}


def save_state(st):
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(st, f)
    os.replace(tmp, STATE_FILE)


def main():
    if not BOT_TOKEN:
        print('!! 请设置 TG_BOT_TOKEN (环境变量或 .env)', file=sys.stderr)
        sys.exit(1)
    try:
        r = api('setMyCommands', {'commands': [{'command': c, 'description': d}
                                               for c, d in BOT_COMMANDS]})
        print('[cmd] setMyCommands', 'ok' if r else 'fail', flush=True)
    except Exception as _e:
        print('[cmd] setMyCommands 失败', _e, flush=True)
    me = api('getMe', {})
    if me:
        print(f'[panghubot] 已上线: @{me.get("username")} ({me.get("id")})', flush=True)
    else:
        print('[panghubot] getMe 失败，继续…', flush=True)
    st = load_state()
    offset = st.get('offset', 0)
    while True:
        try:
            updates = api('getUpdates', {
                'offset': offset, 'timeout': 50,
                'allowed_updates': ['message', 'callback_query'],
            })
            if not updates:
                time.sleep(2)
                continue
            for u in updates:
                offset = u['update_id'] + 1
                try:
                    if 'message' in u:
                        handle_message(u['message'])
                    elif 'callback_query' in u:
                        cb = u['callback_query']
                        api('answerCallbackQuery', {'callback_query_id': cb['id']})
                        handle_callback(cb)
                except Exception:
                    traceback.print_exc()
                st['offset'] = offset
                save_state(st)
            print(f'[poll] {time.strftime("%H:%M:%S")} offset={offset}', flush=True)
        except KeyboardInterrupt:
            print('[panghubot] bye', flush=True)
            break
        except Exception as e:
            err = str(e)
            traceback.print_exc()
            if 'Conflict' in err or '409' in err:
                print('[api] 409 冲突, 重置 offset 重连', flush=True)
                offset = 0  # 丢掉被污染的 offset, 重新抢轮询
                time.sleep(3)
            else:
                time.sleep(5)


if __name__ == '__main__':
    main()
