# -*- coding: utf-8 -*-
"""订阅抓取: 直连 + 镜像回退 (raw.githubusercontent 在部分网络被 302 到死 CDN)"""
import re
import requests

UA_POOL = [
    'ClashforWindows/0.20.39',
    'ClashMetaForAndroid/2.9.0',
    'v2rayNG/1.8.29',
    'Quantumult%20X/1.4.1 (iPhone; iOS 17.2)',
    'Shadowrocket/2.2.19',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15',
]

TIMEOUT = 20
MAX_SIZE = 5 * 1024 * 1024  # 5MB


def _mirror_candidates(url):
    """给 url 生成镜像候选列表 (只对 github 相关做)"""
    out = [url]
    m = re.match(r'https?://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)', url)
    if m:
        user, repo, br, path = m.groups()
        gh = f'{user}/{repo}@{br}/{path}'
        for host in ('gcore.jsdelivr.net', 'testingcf.jsdelivr.net', 'fastly.jsdelivr.net',
                     'cdn.jsdelivr.net'):
            out.append(f'https://{host}/gh/{gh}')
        for proxy in ('gh-proxy.com', 'ghfast.top', 'ghproxy.net'):
            out.append(f'https://{proxy}/{url}')
    m2 = re.match(r'https?://github\.com/([^/]+)/([^/]+)/(?:raw|blob)/([^/]+)/(.+)', url)
    if m2:
        user, repo, br, path = m2.groups()
        gh = f'{user}/{repo}@{br}/{path}'
        for host in ('gcore.jsdelivr.net', 'testingcf.jsdelivr.net'):
            out.append(f'https://{host}/gh/{gh}')
        out.append(f'https://gh-proxy.com/https://raw.githubusercontent.com/{user}/{repo}/{br}/{path}')
    m3 = re.match(r'https?://github\.com/([^/]+)/([^/]+)/raw/refs/heads/([^/]+)/(.+)', url)
    if m3:
        user, repo, br, path = m3.groups()
        gh = f'{user}/{repo}@{br}/{path}'
        for host in ('gcore.jsdelivr.net', 'testingcf.jsdelivr.net'):
            out.append(f'https://{host}/gh/{gh}')
    return out


def fetch_one(url, timeout=TIMEOUT):
    """抓单条, 返回 (text|None, err)"""
    last_err = 'unknown'
    for ua_i, cand in enumerate(_mirror_candidates(url)):
        for ua in (UA_POOL[ua_i % 2], UA_POOL[0]):
            try:
                r = requests.get(cand, timeout=timeout, headers={
                    'User-Agent': ua,
                    'Accept': '*/*',
                    'Accept-Encoding': 'gzip, deflate',
                }, allow_redirects=True, stream=True)
                if r.status_code != 200:
                    last_err = f'HTTP {r.status_code}'
                    continue
                chunks, size = [], 0
                for c in r.iter_content(65536):
                    size += len(c)
                    if size > MAX_SIZE:
                        last_err = 'too large'
                        break
                    chunks.append(c)
                if last_err == 'too large':
                    continue
                raw = b''.join(chunks)
                # 编码猜测
                enc = r.encoding or 'utf-8'
                if enc.lower() in ('iso-8859-1', 'ascii'):
                    enc = 'utf-8'
                try:
                    return raw.decode(enc, 'ignore'), None
                except Exception:
                    return raw.decode('utf-8', 'ignore'), None
            except requests.exceptions.SSLError as e:
                last_err = f'SSL: {str(e)[:60]}'
            except requests.exceptions.Timeout:
                last_err = 'timeout'
            except Exception as e:
                last_err = f'{type(e).__name__}: {str(e)[:60]}'
        # 镜像间稍作喘息
    return None, last_err


def extract_urls(text):
    """从文本提取 http(s) 链接"""
    return re.findall(r'https?://[^\s<>"\']+', text or '')
