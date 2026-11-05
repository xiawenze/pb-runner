#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fofa_pool — 官方 FOFA 多 key 轮换池（额度不够自动换下一把）

钥匙来源：环境变量 FOFA_KEYS（逗号/换行分隔），或文件 data/fofa_keys.txt
状态：data/fofa_state.json（limit / dead / nopoint / ok）
"""
import base64
import json
import os
import time
import urllib.parse

import requests

API = 'https://fofa.info/api/v1/search/all'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
STATE = 'data/fofa_state.json'


def _load_keys():
    raw = os.environ.get('FOFA_KEYS', '')
    ks = [k.strip() for k in raw.replace('\n', ',').split(',') if k.strip()]
    p = 'data/fofa_keys.txt'
    if os.path.exists(p):
        for line in open(p, encoding='utf-8'):
            line = line.strip()
            if line and not line.startswith('#'):
                ks.append(line)
    out, seen = [], set()
    for k in ks:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


class Pool:
    def __init__(self):
        self.keys = _load_keys()
        try:
            self.st = json.load(open(STATE, encoding='utf-8'))
        except Exception:
            self.st = {}
        for k in self.keys:
            self.st.setdefault(k, {'status': 'ok', 'used': 0})
        self.i = 0
        self.save()

    def save(self):
        os.makedirs('data', exist_ok=True)
        tmp = STATE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.st, f)
        os.replace(tmp, STATE)

    def next(self):
        n = len(self.keys)
        for _ in range(n):
            k = self.keys[self.i % n]
            self.i += 1
            if self.st.get(k, {}).get('status') == 'ok':
                return k
        return None

    def mark(self, k, status):
        self.st[k]['status'] = status
        self.save()

    def bump(self, k):
        self.st[k]['used'] = self.st.get(k, {}).get('used', 0) + 1
        self.save()

    def report(self):
        s = {}
        for v in self.st.values():
            s[v['status']] = s.get(v['status'], 0) + 1
        return s, sum(v.get('used', 0) for v in self.st.values())

    def call(self, q, size=100, page=1,
             fields='host,ip,port,title,server', max_try=10):
        """返回 (rows, total, err)。err='ALL_LIMIT' 表示全打满"""
        qb = base64.b64encode(q.encode()).decode()
        last = None
        for _ in range(max_try):
            k = self.next()
            if not k:
                return [], 0, 'ALL_LIMIT'
            try:
                r = requests.get(API, params={'key': k, 'qbase64': qb, 'size': size,
                                              'page': page, 'fields': fields},
                                 headers={'User-Agent': UA}, timeout=40, verify=False)
                j = r.json()
            except Exception as e:
                last = 'NET:' + type(e).__name__
                time.sleep(2)
                continue
            self.bump(k)
            if not j.get('error'):
                return j.get('results') or [], j.get('size') or 0, None
            msg = str(j.get('errmsg') or '')
            if '820041' in msg:
                self.mark(k, 'limit')
                continue
            if '-700' in msg:
                self.mark(k, 'dead')
                continue
            if '820031' in msg:
                self.mark(k, 'nopoint')
                continue
            if '45012' in msg:
                time.sleep(18)
                last = msg
                continue
            return [], 0, msg
        return [], 0, last


def to_rows(results, fields='host,ip,port,title,server'):
    names = [x.strip() for x in fields.split(',')]
    rows = []
    for item in results or []:
        if isinstance(item, list):
            rows.append(dict(zip(names, [str(x) if x is not None else '' for x in item])))
        elif isinstance(item, dict):
            rows.append({k: str(item.get(k) or '') for k in names})
    return rows
