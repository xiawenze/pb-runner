#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""harvest_daily.py — 深度挖矿：多指纹 × 多页 + 后端探测 + 首页抓取 → 全收 → 验订阅 → 真测活"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

QUERIES = [
    'body="SUB_STORE_FRONTEND_BACKEND_PATH"',
    'header="X-Powered-By: Sub-Store"',
    'body="/api/v1/client/subscribe?token="',
    'body="/api/subs"',
    'body="/api/v1/guest/comm/config"',
    'body="/api/public/profiles"',
    'title="MiSub"',
    'body="订阅" && body="clash"',
]
QUAKE_QUERIES = [
    'body:"Sub-Store"',
    'body:"/api/public/profiles"',
    'body:"/api/v1/client/subscribe?token="',
    'body:"/api/subs"',
    'app:"v2board"',
]
STATE = 'data/harvest_state.json'
PAGES = 2
PROBE_MAX = 200


def load_state():
    try:
        return json.load(open(STATE, encoding='utf-8'))
    except Exception:
        return {}


def save_state(st):
    os.makedirs('data', exist_ok=True)
    tmp = STATE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, STATE)


def _merge(rows, hosts, seen):
    n = 0
    for x in rows:
        h = str(x.get('host') or '')
        if h and h not in seen:
            seen.add(h)
            hosts.append(x)
            n += 1
    return n


def run(cfg, log=print):
    from core import fofa, quake, harvest, realtest

    fkey = (cfg.get('fofa_key') or '').strip()
    qkey = (cfg.get('quake_key') or '').strip()
    if fkey:
        os.environ['FOFA_PROXY_KEY'] = fkey      # 关键：注入后 fofa.search 才走代理通道
    if not fkey and not qkey:
        return {'ok': False, 'err': '既没有 FOFA 也没有 Quake key'}

    hosts, seen, hits = [], set(), {}

    # ---- 1) FOFA 多指纹 × 多页
    if fkey:
        for q in QUERIES:
            for page in range(1, PAGES + 1):
                try:
                    r = fofa.search('', '', q, size=100, page=page)
                except Exception as e:
                    log('[harvest] FOFA %s p%d 失败: %s' % (q[:24], page, str(e)[:50]))
                    break
                hits[q] = max(hits.get(q, 0), r.get('total') or 0)
                n = _merge(r['rows'], hosts, seen)
                log('[harvest] FOFA %-34s p%d +%d (hit=%s)' % (q[:34], page, n, r.get('total')))
                if n == 0:
                    break
                time.sleep(1.2)

    # ---- 1b) FOFA 官方钥匙池（多把轮换，额度不够自动换）
    if not hosts:
        from core import fofa_pool
        pool = fofa_pool.Pool()
        if pool.keys:
            log('[harvest] 代理不可用，改用官方钥匙池 %d 把' % len(pool.keys))
            for q in QUERIES:
                rows, total, err = pool.call(q, size=100)
                if err:
                    log('[harvest] 池 %-30s %s' % (q[:30], str(err)[:40]))
                    if err == 'ALL_LIMIT':
                        break
                    continue
                hits[q] = max(hits.get(q, 0), total or 0)
                n = _merge(fofa_pool.to_rows(rows), hosts, seen)
                log('[harvest] 池 %-32s +%d (hit=%s)' % (q[:32], n, total))
                if n == 0:
                    continue
            st_rep = pool.report()
            log('[harvest] 池状态 %s' % (st_rep,))

    # ---- 2) 空 → Quake 兜底
    engine = 'fofa' if hosts else ('quake' if qkey else 'none')
    if not hosts and qkey:
        for q in QUAKE_QUERIES:
            try:
                rr = quake.search(qkey, q, size=100)
            except Exception as e:
                log('[harvest] Quake %s 失败: %s' % (q[:24], str(e)[:50]))
                continue
            hits[q] = max(hits.get(q, 0), rr.get('total') or 0)
            n = _merge(rr['rows'], hosts, seen)
            log('[harvest] Quake %-32s +%d (hit=%s)' % (q[:32], n, rr.get('total')))
            time.sleep(1.2)

    log('[harvest] %s 共 %d 台主机' % (engine, len(hosts)))
    if not hosts:
        return {'ok': False, 'err': '%s 无命中（可能额度/平台异常）' % engine}

    # ---- 3) 后端探测（/api/subs、MiSub），全收
    cands = []
    with ThreadPoolExecutor(max_workers=24) as ex:
        for urls, note in ex.map(harvest.probe_host, [x['host'] for x in hosts[:PROBE_MAX] if x.get('host')]):
            if note:
                cands.extend(urls)
    raw_n = len(cands)
    cands = list(dict.fromkeys(cands))
    log('[harvest] 后端探测 %d 台 → %d 条（原始 %d）' % (min(len(hosts), PROBE_MAX), len(cands), raw_n))

    # ---- 4) 没有结果就抓首页正文（深度挖）
    if len(cands) < 5 and hosts:
        from core import subman
        import requests

        def grab(h):
            host = str(h.get('host') or '').strip()
            port = str(h.get('port') or '')
            if not host:
                return []
            if not host.startswith(('http://', 'https://')):
                host = ('https' if port in ('443', '8443') else 'http') + '://' + host
                if port and port not in ('80', '443'):
                    host += ':' + port
            try:
                r = requests.get(host, timeout=6, verify=False,
                                 headers={'User-Agent': 'clash-verge/v1.6.0'})
                return subman.harvest_urls((r.text or '')[:200000])
            except Exception:
                return []

        extra = []
        with ThreadPoolExecutor(max_workers=16) as ex:
            for got in ex.map(grab, hosts[:80]):
                extra.extend(got)
        cands.extend(extra)
        cands = list(dict.fromkeys(cands))
        log('[harvest] 首页抓取补充后 %d 条' % len(cands))

    if not cands:
        return {'ok': True, 'engine': engine, 'raw': raw_n, 'uniq': 0, 'valid': [], 'hits': hits}

    # ---- 5) 验订阅 + 真测活
    picked = cands[:60]
    try:
        res = realtest.verify_subs(picked, per_sub=3, timeout_ms=3500, ready_wait=220)
    except Exception as e:
        log('[harvest] 测活异常: %s' % str(e)[:80])
        res = {}

    valid = []
    for u in picked:
        v = res.get(u) or {}
        if not (v.get('nodes') or 0):
            continue
        valid.append({
            'url': u,
            'brand': (v.get('brand') or '').strip(),
            'flag': (v.get('flag') or '').strip(),
            'nodes': v.get('nodes') or 0,
            'alive': v.get('alive') or 0,
            'best': v.get('best') or 0,
            'rest_gb': v.get('rest_gb'),
            'total_gb': v.get('total_gb'),
            'expire_date': v.get('expire_date'),
            'err': v.get('err') or '',
        })
    valid.sort(key=lambda x: (-x['alive'], -x['nodes']))
    log('[harvest] 有效订阅 %d 条 · 有活节点 %d 条'
        % (len(valid), sum(1 for x in valid if x['alive'])))

    st = load_state()
    st['last_run'] = int(time.time())
    st['last_result'] = valid[:80]
    st['last_hits'] = hits
    st['engine'] = engine
    save_state(st)
    return {'ok': True, 'engine': engine, 'raw': raw_n, 'uniq': len(cands),
            'valid': valid, 'hits': hits}


def report_text(s):
    if not s.get('ok'):
        return '🕗 深度挖矿失败：%s' % s.get('err')
    valid = s.get('valid') or []
    alive = [x for x in valid if x['alive'] > 0]
    lines = ['🕗 <b>深度挖矿完成</b>（引擎 %s）' % s.get('engine'),
             '抓到 %d 条（未去重 %d）· 有效订阅 %d · 有活节点 %d'
             % (s.get('uniq', 0), s.get('raw', 0), len(valid), len(alive)), '']
    for i, x in enumerate(alive[:10], 1):
        name = (x['flag'] + x['brand']).strip() or '未识别'
        bits = ['%d. %s' % (i, name), '%d节点' % x['nodes'], '活%d' % x['alive']]
        if x.get('total_gb'):
            bits.append('剩%sG' % x['rest_gb'])
        if x.get('expire_date'):
            bits.append('到%s' % x['expire_date'])
        lines.append(' · '.join(bits))
        lines.append('   ' + x['url'])
    if len(alive) > 10:
        lines.append('… 还有 %d 条' % (len(alive) - 10))
    return '\n'.join(lines)
