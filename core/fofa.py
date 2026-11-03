#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fofa — FOFA API 客户端 (个人中心 email + API Key)"""
import base64
import os
import urllib.parse

import requests

API = 'https://fofa.info/api/v1/search/all'
FIELDS = 'host,ip,port,protocol,title,server,body'
TIMEOUT = 40
PROXY_BASE = os.environ.get('FOFA_BASE', 'https://fofa.xiaomochen.tech').rstrip('/')
BROWSER_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')


def _proxy_search(pkey, q, size, page, with_body):
    """索迹中转平台 (Bearer 平台密钥, 必须带浏览器 UA 否则 CF 1010)"""
    qbase64 = base64.b64encode(q.encode('utf-8')).decode()
    fields = 'host,ip,port,title,server' + (',body' if with_body else '')
    params = {'qbase64': qbase64, 'size': min(int(size) or 100, 100),
              'page': page, 'fields': fields}
    try:
        r = requests.get(PROXY_BASE + '/api/v1/search/all', params=params,
                         headers={'Authorization': 'Bearer ' + pkey, 'User-Agent': BROWSER_UA},
                         timeout=TIMEOUT)
        j = r.json()
    except Exception as e:
        raise FofaError(f'网络错误: {type(e).__name__} {str(e)[:60]}')
    if j.get('error') in (True, 1) or j.get('code') in ('unauthorized', 'key_expired'):
        raise FofaError(str(j.get('code') or j.get('msg') or 'platform error')[:120])
    res = j.get('result') or []
    names = [x.strip() for x in fields.split(',')]
    rows = []
    for item in res:
        if isinstance(item, list):
            row = dict(zip(names, [str(x) if x is not None else '' for x in item]))
        elif isinstance(item, dict):
            row = {k: (str(item.get(k)) if item.get(k) is not None else '') for k in names}
        else:
            continue
        rows.append(row)
    return {'total': int(j.get('size') or len(rows) or 0), 'rows': rows}


def search(email, key, q, size=100, page=1, with_body=True):
    """返回 {'total':int,'rows':[{'host','ip','port','title','server','body'}]}
    出错 raise FofaError(msg)"""
    pkey = os.environ.get('FOFA_PROXY_KEY', '').strip()
    if pkey and not (email and key):
        return _proxy_search(pkey, q, size, page, with_body)
    qbase64 = base64.b64encode(q.encode('utf-8')).decode()
    fields = FIELDS if with_body else 'host,ip,port,protocol,title,server'
    params = {
        'email': email, 'key': key, 'qbase64': qbase64,
        'size': size, 'page': page, 'fields': fields,
    }
    for attempt_fields in (fields, None):
        if attempt_fields is None:
            params.pop('fields', None)
        try:
            r = requests.get(API, params=params, timeout=TIMEOUT)
            j = r.json()
        except Exception as e:
            raise FofaError(f'网络错误: {type(e).__name__} {str(e)[:60]}')
        if j.get('error') or (j.get('errmsg') and not j.get('results')):
            msg = j.get('errmsg') or 'query error'
            if 'fields' in params and 'field' in str(msg).lower():
                continue  # 字段不支持, 降级重试
            raise FofaError(str(msg)[:120])
        rows = []
        names = [x.strip() for x in (params.get('fields') or 'host,ip,port').split(',')]
        for item in j.get('results') or []:
            if not isinstance(item, list):
                continue
            row = dict(zip(names, [str(x) if x is not None else '' for x in item]))
            rows.append(row)
        return {'total': j.get('size', len(rows)) or len(rows), 'rows': rows}
    raise FofaError('fields 降级后仍失败')


class FofaError(Exception):
    pass


def render_rows(rows, blockwords=()):
    """渲染成 txt 行"""
    out = []
    for r in rows:
        line = ' | '.join(str(r.get(k) or '') for k in ('host', 'ip', 'port', 'title', 'server'))
        line = line.replace('\n', ' ').replace('\r', ' ').strip()
        if not line.strip(' |'):
            continue
        if any(w and w in line for w in blockwords):
            continue
        out.append(line[:300])
    return out
