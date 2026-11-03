#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""subman — 订阅库管理: 增删/收藏/刷新/userinfo 解析/链接收割"""
import base64
import json
import os
import re
import time

import requests

from . import airport

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(HERE), 'data')
os.makedirs(DATA_DIR, exist_ok=True)
SUBS_FILE = os.path.join(DATA_DIR, 'subs.json')

UA_SUB = 'clash-verge/v1.6.0'
UA_FALLBACK = ['clash-verge/v1.6.0', 'ClashMetaForAndroid/2.10.1',
               'v2rayN/6.23', 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)']

# ---------------------------------------------------------------- userinfo
def parse_userinfo(hdr_value):
    """subscription-userinfo: upload=..; download=..; total=..; expire=.."""
    if not hdr_value:
        return None
    d = {}
    for kv in re.split(r'[;\s]+', hdr_value.strip()):
        if '=' in kv:
            k, v = kv.split('=', 1)
            try:
                d[k.strip().lower()] = float(v.strip())
            except ValueError:
                pass
    if not d:
        return None
    up = int(d.get('upload', 0))
    down = int(d.get('download', 0))
    total = int(d.get('total', 0))
    exp = int(d.get('expire', 0))
    used = up + down
    return {
        "upload_gb": round(up / 1e9, 2),
        "used_gb": round(used / 1e9, 2),
        "total_gb": round(total / 1e9, 2) if total else None,
        "rest_gb": round((total - used) / 1e9, 2) if total else None,
        "expire": exp,
        "expire_date": time.strftime('%Y-%m-%d', time.localtime(exp)) if exp > 0 else None,
        "expired": bool(exp and exp < time.time()),
        "over_quota": bool(total and used >= total),
    }


def fetch_sub(url, timeout=25):
    """拉订阅: 返回 (text, userinfo_dict|None, meta|None)，meta 含 brand/title/web_page/remark
    多 UA 轮换, 200 才收, 抓 subscription-userinfo 响应头。"""
    last_err = None
    for ua in UA_FALLBACK:
        try:
            r = requests.get(url, timeout=timeout, headers={
                'User-Agent': ua,
                'Accept': '*/*',
            }, allow_redirects=True)
            if r.status_code != 200:
                last_err = f'HTTP {r.status_code}'
                continue
            ui = parse_userinfo(r.headers.get('subscription-userinfo')
                                or r.headers.get('Subscription-Userinfo')
                                or r.headers.get('Subscription-User-Info'))
            text = r.text or ''
            if len(text.strip()) < 8:
                last_err = 'empty body'
                continue
            meta = profile_meta(r.headers)
            meta['brand'] = airport.display(url, text,
                                            meta.get('title', ''),
                                            meta.get('web_page', ''))
            return text, ui, meta
        except Exception as e:
            last_err = f'{type(e).__name__}: {str(e)[:50]}'
    return None, None, last_err


def profile_meta(headers):
    """读 Clash 系订阅的 profile-* 响应头，机场常在这里写牌子名与官网"""
    h = {k.lower(): v for k, v in (headers or {}).items()}

    def dec(v):
        """格式 1: 'base64:XXXX'  格式 2: 裸字符串（英文/中文/去引号）"""
        if not v:
            return ""
        v = v.strip()
        if v.lower().startswith("base64:"):
            raw = v.split(":", 1)[1].strip()
            pad = "=" * (-len(raw) % 4)
            try:
                return base64.b64decode(raw + pad).decode("utf-8", "ignore").strip()[:200]
            except Exception:
                return ""
        if v.startswith('"') and v.endswith('"') and len(v) > 2:
            v = v[1:-1]
        return v[:200]

    return {
        "title": dec(h.get("profile-title")),
        "web_page": dec(h.get("profile-web-page-url")),
        "remark": dec(h.get("profile-remark")),
        "interval": (h.get("profile-update-interval") or "").strip(),
        "announce": dec(h.get("announce")),
    }


def brand_of(url, text="", headers=None):
    m = profile_meta(headers)
    return airport.display(url, text, m.get("title", ""), m.get("web_page", ""))


# ---------------------------------------------------------------- 收割
SUB_URL_RE = re.compile(
    r'https?://[^\s"\'<>\)\]，。；、\]]+?'
    r'(?:api/v1/client/subscribe\?[^\s"\'<>\)\]]*token=[0-9A-Za-z]{12,}'
    r'|/link/[0-9A-Za-z]{8,}'
    r'|/s/[0-9a-fA-F]{16,}'
    r'|/sub/[0-9A-Za-z_\-]{3,40}'
    r'|\?sub=[0-9A-Za-z]{12,}'
    r'|[0-9A-Za-z]/[0-9A-Za-z]{6,}\?target=)'
)
SUB_HINT_RE = re.compile(r'(subscribe|subscription-userinfo|/link/|/api/v1/client)', re.I)


def harvest_urls(text):
    """从任意文本(搜索结果 body 等)里抽疑似订阅链接"""
    if not text:
        return []
    found = []
    seen = set()
    for m in SUB_URL_RE.finditer(text):
        u = m.group(0).rstrip('\\').rstrip(',')
        if u not in seen and len(u) < 500:
            seen.add(u)
            found.append(u)
    # 兜底: 含强特征的裸链接
    for m in re.finditer(r'https?://[^\s"\'<>\)\]]{10,300}', text):
        u = m.group(0)
        if u not in seen and SUB_HINT_RE.search(u):
            seen.add(u)
            found.append(u)
    return found[:50]


# ---------------------------------------------------------------- 库
def _now():
    return int(time.time())


def host_of(url):
    m = re.match(r'https?://([^/:]+)', url or '')
    return m.group(1) if m else (url or '')[:30]


class Store:
    def __init__(self, path=SUBS_FILE):
        self.path = path
        self.d = {}
        self.load()

    def load(self):
        if os.path.exists(self.path):
            try:
                self.d = json.load(open(self.path, encoding='utf-8'))
            except Exception:
                self.d = {}

    def save(self):
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    # ---- CRUD
    def add(self, url, name=None):
        url = url.strip()
        if not url.startswith(('http://', 'https://')):
            return False, '不是 http(s) 链接'
        if url in self.d:
            return False, '已在库里'
        self.d[url] = {
            "name": (name or host_of(url))[:48],
            "fav": False,
            "added": _now(),
            "last": None,
        }
        self.save()
        return True, 'ok'

    def remove(self, url):
        if url in self.d:
            del self.d[url]
            self.save()
            return True
        return False

    def get(self, url):
        return self.d.get(url)

    def urls(self):
        return list(self.d.keys())

    def set_fav(self, url, fav):
        if url in self.d:
            self.d[url]['fav'] = bool(fav)
            self.save()

    def rename(self, url, name):
        if url in self.d:
            self.d[url]['name'] = name[:48]
            self.save()

    def record(self, url, payload):
        if url in self.d:
            self.d[url]['last'] = payload
            self.d[url]['last']['ts'] = _now()
            self.save()

    # ---- 视图
    def sorted_urls(self, sort='expire', only_fav=False):
        items = list(self.d.items())

        def key(kv):
            u, v = kv
            last = v.get('last') or {}
            if sort == 'rest':
                return -(last.get('rest_gb') or -1)
            if sort == 'nodes':
                return -(last.get('nodes') or 0)
            if sort == 'added':
                return -v.get('added', 0)
            # expire: 永不过期(0)放最后
            e = last.get('expire') or 0
            return e if e else 9e11

        items.sort(key=key)
        if only_fav:
            items = [kv for kv in items if kv[1].get('fav')]
        return [u for u, _ in items]
