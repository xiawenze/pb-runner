#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""upload — 把生成的订阅文件传成公网直链 (x0.at / 0x0.st / tmpfiles 兜底)"""
import json

import requests

UA = 'curl/8.4.0'


def upload_text(filename, content, timeout=45):
    """返回 (url, err)"""
    data = content.encode() if isinstance(content, str) else content
    files = {'file': (filename, data, 'application/octet-stream')}
    errs = []
    for url in ('https://x0.at', 'https://0x0.st'):
        try:
            r = requests.post(url, files=files, timeout=timeout, headers={'User-Agent': UA})
            txt = (r.text or '').strip()
            if r.status_code == 200 and txt.startswith('http'):
                return txt.splitlines()[0].strip(), None
            errs.append(f'{url} → HTTP {r.status_code} {txt[:40]}')
        except Exception as e:
            errs.append(f'{url} → {str(e)[:50]}')
    try:
        r = requests.post('https://tmpfiles.org/api/v1/upload', files=files,
                          timeout=timeout, headers={'User-Agent': UA})
        j = r.json()
        u = (((j or {}).get('data') or {}).get('url') or '')
        if u:
            return u.replace('tmpfiles.org/', 'tmpfiles.org/dl/'), None
        errs.append('tmpfiles → 无 url')
    except Exception as e:
        errs.append(f'tmpfiles → {str(e)[:50]}')
    return None, '; '.join(errs)
