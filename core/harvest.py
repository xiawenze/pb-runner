#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""harvest - 从 FOFA/Quake 命中主机里挖「真订阅」

两路:
  1) Sub-Store 后端 /api/subs 未鉴权 → 里面存着用户真实订阅 URL（命中率最高）
  2) MiSub /api/public/profiles → profileToken + customId 拼订阅地址
"""
import json
import re

import requests

UA_LIST = ['clash-verge/v1.6.0', 'ClashMetaForAndroid/2.10.1', 'curl/8.4.0']
TIMEOUT = 7

SUB_RE = re.compile(
    r'https?://[^\s"\'<>()\[\]{},;]+'
    r'(?:/api/v1/client/subscribe\?[^\s"\'<>()\[\]{},;]*token=[A-Za-z0-9]{8,}'
    r'|/api/sub/[A-Za-z0-9_\-]{6,40}'
    r'|/sub\?[^\s"\'<>()\[\]{},;]*token=[A-Za-z0-9]{8,}'
    r'|/s/[A-Za-z0-9_\-]{16,64}'
    r'|/link/[A-Za-z0-9_\-]{16,64}'
    r'|/subscribe\?[^\s"\'<>()\[\]{},;]*token=[A-Za-z0-9]{8,}'
    r'|/[A-Za-z0-9_\-]{8,40}\?target=(?:clash|clashmeta|singbox|surge)'
    r'|/api/v1/liangxin\?[^\s"\'<>()\[\]{},;]*'
    r'|/api/v1/pq/[A-Za-z0-9]+'
    r'|/haut/[A-Za-z0-9]+)')


def bases(host):
    """host 可能是 'ip:port' / 'https://x.com' / 'x.com'，展开成候选 base"""
    h = str(host or '').strip().rstrip('/')
    if not h:
        return []
    if h.startswith(('http://', 'https://')):
        b = h
        out = [b]
        if ':' not in b.split('//')[1]:
            out += [b + ':3000', b.replace('https://', 'http://')]
        return out
    out = ['https://' + h, 'http://' + h]
    if ':' not in h:
        out += ['http://' + h + ':3000', 'https://' + h + ':3000']
    return out


def _get(url, ua=None, timeout=TIMEOUT):
    try:
        r = requests.get(url, timeout=timeout, verify=False, allow_redirects=True,
                         headers={'User-Agent': ua or UA_LIST[0], 'Accept': '*/*'})
        return r
    except Exception:
        return None


def probe_host(host, deep=False):
    """返回 (urls, note)
    urls: 从该主机挖到的订阅地址列表
    note: 'sub-store' / 'misub' / '' 供报表用
    """
    urls, notes = [], []
    for base in bases(host):
        # ---- Sub-Store 后端
        for ua in UA_LIST[:2]:
            r = _get(base + '/api/subs', ua=ua)
            if not r or r.status_code != 200:
                continue
            txt = (r.text or '')[:400000]
            try:
                j = json.loads(txt)
            except Exception:
                j = None
            if isinstance(j, dict) and (j.get('status') == 'success' or 'data' in j):
                notes.append('sub-store')
                for s in (j.get('data') or j.get('subs') or []):
                    if not isinstance(s, dict):
                        continue
                    if s.get('url'):
                        urls.append(s['url'])
                    for m in SUB_RE.findall(str(s.get('content') or '')):
                        urls.append(m)
            for m in SUB_RE.findall(txt):
                urls.append(m)
            if urls:
                break
        # ---- MiSub
        r = _get(base + '/api/public/profiles', ua='curl/8.4.0')
        if r and r.status_code == 200:
            try:
                j = json.loads(r.text or '{}')
            except Exception:
                j = {}
            if isinstance(j, dict) and j.get('success'):
                tok = (j.get('config') or {}).get('profileToken') or ''
                for p in (j.get('data') or []):
                    cid = p.get('customId') if isinstance(p, dict) else None
                    if tok and cid and not str(cid).startswith('http'):
                        urls.append('%s/%s/%s' % (base, tok, cid))
                if urls:
                    notes.append('misub')
        if urls:
            break
    # 去重
    out, seen = [], set()
    for u in urls:
        u = str(u).strip()
        if not u.startswith('http') or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out[:40], '+'.join(dict.fromkeys(notes))
