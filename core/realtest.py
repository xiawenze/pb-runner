#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""realtest - 用 mihomo 真握手测节点存活（TCP 快测会假阳性，这个不会）

用法:
    res = test_nodes(nodes)          # {节点名: 延迟ms 或 None}
    res = test_nodes(nodes, sample=40)
"""
import json
import os
import random
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request

OK_TYPES = {"ss", "ssr", "vmess", "vless", "trojan", "hysteria", "hysteria2", "tuic"}


def find_mihomo():
    for p in (os.environ.get("MIHOMO_BIN", ""), "/usr/local/bin/mihomo",
              "/usr/bin/mihomo", "./mihomo", os.path.expanduser("~/mihomo")):
        if p and os.path.exists(p) and os.access(p, os.X_OK):
            return p
    return shutil.which("mihomo") or ""


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _wait_ready(api, seconds):
    end = time.time() + seconds
    while time.time() < end:
        try:
            urllib.request.urlopen(api + "/version", timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False



def _wait_proxies(api, names, seconds):
    """等节点真正出现在 /proxies 里（/version 通了不代表配置加载完）"""
    end = time.time() + seconds
    want = set(names)
    while time.time() < end:
        try:
            with urllib.request.urlopen(api + "/proxies", timeout=3) as r:
                got = set(json.load(r).get("proxies", {}).keys())
            if want & got:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _delay(api, name, timeout_ms, url):
    q = urllib.parse.quote(name, safe="")
    u = (api + "/proxies/" + q + "/delay?timeout=" + str(timeout_ms)
         + "&url=" + urllib.parse.quote(url, safe=""))
    try:
        with urllib.request.urlopen(u, timeout=timeout_ms / 1000.0 + 8) as r:
            d = json.load(r)
            if isinstance(d, dict) and d.get("delay", 0) > 0:
                return d["delay"]
    except Exception:
        pass
    return None


def test_nodes(nodes, sample=None, timeout_ms=4500, concurrency=16,
               url="http://www.gstatic.com/generate_204", progress=None,
               ready_wait=180):
    """返回 (result, info)
    result: {原名: 延迟ms 或 None}
    info:   {'mode': 'mihomo'/'tcp', 'tested': n, 'alive': n, 'ok': bool}
    """
    bin_path = find_mihomo()
    if not bin_path:
        return {}, {"mode": "none", "ok": False, "err": "找不到 mihomo 二进制"}
    picked = [n for n in nodes if n.get("type") in OK_TYPES]
    if sample and len(picked) > sample:
        picked = random.sample(picked, sample)
    if not picked:
        return {}, {"mode": "mihomo", "ok": False, "err": "没有可测节点"}

    work = tempfile.mkdtemp(prefix="mh-")
    port = _free_port()
    api = "http://127.0.0.1:%d" % port
    proxies, mapping = [], {}
    for i, n in enumerate(picked):
        p = dict(n)
        name = "n%03d" % i
        p["name"] = name
        p.setdefault("skip-cert-verify", True)
        proxies.append(p)
        mapping[name] = n.get("name") or "%s:%s" % (n.get("server"), n.get("port"))
    cfg = {
        "mixed-port": _free_port(),
        "allow-lan": False,
        "mode": "global",
        "log-level": "silent",
        "external-controller": "127.0.0.1:%d" % port,
        "dns": {"enable": False},
        "proxies": proxies,
        "proxy-groups": [{"name": "G", "type": "select",
                          "proxies": [p["name"] for p in proxies]}],
        "rules": ["MATCH,G"],
    }
    cfg_path = os.path.join(work, "cfg.yaml")
    try:
        import yaml
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    except Exception as e:
        shutil.rmtree(work, ignore_errors=True)
        return {}, {"mode": "mihomo", "ok": False, "err": "yaml 写配置失败 %s" % e}

    proc = None
    try:
        proc = subprocess.Popen([bin_path, "-f", cfg_path, "-d", work],
                                stdout=open(os.path.join(work, "mh.log"), "wb"),
                                stderr=subprocess.STDOUT)
        if not _wait_ready(api, ready_wait) or not _wait_proxies(api, [p["name"] for p in proxies], ready_wait):
            log = ""
            try:
                log = open(os.path.join(work, "mh.log"), errors="ignore").read()[-200:]
            except Exception:
                pass
            return {}, {"mode": "mihomo", "ok": False, "err": "mihomo 未就绪 %s" % log}

        from concurrent.futures import ThreadPoolExecutor
        res = {}
        done = 0
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            futs = {ex.submit(_delay, api, nm, timeout_ms, url): nm for nm in mapping}
            for f in futs:
                pass
            for f, nm in futs.items():
                try:
                    res[nm] = f.result(timeout=timeout_ms / 1000.0 + 15)
                except Exception:
                    res[nm] = None
                done += 1
                if progress and (done % 5 == 0 or done == len(futs)):
                    try:
                        progress(done, len(futs))
                    except Exception:
                        pass

        out = {mapping[nm]: d for nm, d in res.items()}
        alive = sum(1 for d in res.values() if d)
        return out, {"mode": "mihomo", "ok": True, "tested": len(res), "alive": alive}
    finally:
        try:
            if proc:
                proc.terminate()
                proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        shutil.rmtree(work, ignore_errors=True)


def _doh(name, timeout=6):
    """阿里 DoH 解析真实 A 记录（绕开本地 fake-IP）"""
    for base in ("https://dns.alidns.com/resolve", "https://1.1.1.1/dns-query"):
        try:
            u = base + "?" + urllib.parse.urlencode({"name": name, "type": "A"})
            req = urllib.request.Request(u, headers={"Accept": "application/dns-json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                j = json.load(r)
            for a in j.get("Answer", []):
                if a.get("type") == 1 and a.get("data"):
                    return a["data"]
        except Exception:
            continue
    return None


def resolve_servers(nodes, workers=6):
    """把节点里是域名的 server 换成真实 IP（失败则保持原样），返回新列表"""
    need = {}
    for n in nodes:
        s = str(n.get("server") or "")
        if s and not s.replace(".", "").isdigit():
            need[s] = n.get("servername") or s
    if not need:
        return nodes
    from concurrent.futures import ThreadPoolExecutor
    got = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for host, ip in zip(list(need), ex.map(_doh, list(need))):
            got[host] = ip
    out = []
    for n in nodes:
        n = dict(n)
        s = str(n.get("server") or "")
        if not s.replace(".", "").isdigit():
            ip = got.get(s)
            if ip:
                n.setdefault("servername", need.get(s, s))
                n["server"] = ip
        out.append(n)
    return out


def verify_subs(urls, per_sub=4, timeout_ms=4000, ready_wait=180):
    """对一批订阅地址做「拉取 + 抽节点真测」，返回 {url: {'nodes':n,'alive':a,'best':ms,'err':str}}

    一次 mihomo 启动测完全部，比一条一条测快得多。
    """
    from .parse import parse_subscription
    import requests
    from concurrent.futures import ThreadPoolExecutor

    def _fetch(u):
        try:
            r = requests.get(u, timeout=15, verify=False, allow_redirects=True,
                             headers={'User-Agent': 'clash-verge/v1.6.0'})
            if r.status_code != 200 or len((r.text or '').strip()) < 20:
                return u, [], 'HTTP %s' % r.status_code
            res = parse_subscription(r.text)
            nodes = res[0] if isinstance(res, tuple) else res
            return u, nodes, ''
        except Exception as e:
            return u, [], type(e).__name__

    fetched = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        fetched = list(ex.map(_fetch, urls))

    pool, owner = [], {}
    for u, nodes, err in fetched:
        good = [n for n in nodes if n.get('type') in OK_TYPES][:per_sub]
        for i, n in enumerate(good):
            n2 = dict(n)
            name = 'v%03d' % len(pool)
            n2['name'] = name
            owner[name] = u
            pool.append(n2)

    result = {}
    for u, nodes, err in fetched:
        result[u] = {'nodes': len([n for n in nodes if n.get('type') in OK_TYPES]),
                     'alive': 0, 'best': 0, 'err': err}
    if pool:
        real, info = test_nodes(pool, timeout_ms=timeout_ms, ready_wait=ready_wait)
        for nm, d in (real or {}).items():
            u = owner.get(nm)
            if u and d:
                result[u]['alive'] += 1
                if not result[u]['best'] or d < result[u]['best']:
                    result[u]['best'] = d
    return result
