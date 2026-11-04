#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""quake — 360 Quake API v3 客户端 (X-QuakeToken)"""
import requests

API = 'https://quake.360.net/api/v3/search/quake_service'
TIMEOUT = 40


def search(token, q, size=10, start=0):
    """返回 {'total':int,'rows':[{'host','ip','port','title','server','body'}]}
    出错 raise QuakeError(msg)"""
    try:
        r = requests.post(API, json={
            'query': q, 'start': start, 'size': size,
            'ignore_cache': False, 'include': [],
        }, headers={'X-QuakeToken': token}, timeout=TIMEOUT)
        j = r.json()
    except Exception as e:
        raise QuakeError(f'网络错误: {type(e).__name__} {str(e)[:60]}')
    code = j.get('code')
    if code != 0:
        raise QuakeError(f'[{code}] {str(j.get("message") or j.get("msg") or "error")[:120]}')
    rows = []
    for it in j.get('data') or []:
        svc = it.get('service') or {}
        http = svc.get('http') or {}
        rows.append({
            'ip': str(it.get('ip') or ''),
            'port': str(it.get('port') or ''),
            'host': str(it.get('hostname') or http.get('host') or it.get('ip') or ''),
            'title': str(http.get('title') or ''),
            'server': str(http.get('server') or svc.get('name') or ''),
            'body': str(http.get('body') or http.get('html') or '')[:120000],
        })
    total = (((j.get('meta') or {}).get('pagination') or {}).get('total')) or len(rows)
    return {'total': total, 'rows': rows}


class QuakeError(Exception):
    pass


def render_rows(rows, blockwords=()):
    from core.fofa import render_rows as _r
    return _r(rows, blockwords)
