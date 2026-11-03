#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""shortlink — 短链生成 (is.gd → tinyurl 兜底) + 本地登记簿"""
import json
import os
import re

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(HERE), 'data')
os.makedirs(DATA_DIR, exist_ok=True)
REG_FILE = os.path.join(DATA_DIR, 'shortlinks.json')


def shorten(url, timeout=15):
    """返回 (short_url|None, err|None)"""
    if not re.match(r'https?://', url):
        return None, '不是 http(s) 链接'
    try:
        r = requests.get('https://is.gd/create.php',
                         params={'format': 'json', 'url': url}, timeout=timeout)
        j = r.json()
        if j.get('shorturl'):
            return j['shorturl'], None
    except Exception:
        pass
    try:
        r = requests.get('https://tinyurl.com/api-create.php',
                         params={'url': url}, timeout=timeout)
        if r.status_code == 200 and r.text.startswith('http'):
            return r.text.strip(), None
    except Exception:
        pass
    return None, 'is.gd 和 tinyurl 都失败'


class ShortStore:
    def __init__(self, path=REG_FILE):
        self.path = path
        self.d = []
        if os.path.exists(path):
            try:
                self.d = json.load(open(path, encoding='utf-8'))
            except Exception:
                self.d = []

    def save(self):
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def add(self, long_url, short_url):
        self.d.append({'long': long_url, 'short': short_url,
                       'ts': int(__import__('time').time())})
        self.save()

    def remove(self, idx):
        if 0 <= idx < len(self.d):
            item = self.d.pop(idx)
            self.save()
            return item
        return None
