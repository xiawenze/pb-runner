# -*- coding: utf-8 -*-
"""节点测活: 临时拉起 mihomo, 用 group delay API 并发测延迟"""
import os
import json
import time
import random
import shutil
import subprocess
import tempfile
from urllib.parse import quote

import requests

DEFAULT_BIN = os.environ.get('MIHOMO_BIN', '')
GEOIP_SRC = '/root/.config/mihomo/geoip.metadb'


def _find_bin():
    """按真执行验证找 mihomo (挂载盘 noexec 时 which 命中也可能跑不动)"""
    cands = [DEFAULT_BIN, '/usr/local/bin/mihomo', '/tmp/mh/mihomo']
    try:
        import shutil
        w = shutil.which('mihomo')
        if w:
            cands.insert(0, w)
    except Exception:
        pass
    for c in cands:
        if c and os.path.exists(c):
            try:
                r = subprocess.run([c, '-v'], capture_output=True, timeout=30)
                if r.returncode == 0:
                    return c
            except Exception:
                continue
    return None
TEST_URL = 'http://www.gstatic.com/generate_204'
GROUP = 'TEST-ALL'


def _cfg_yaml(nodes):
    from core.gen import _clash_proxy, _yval
    lines = ['mixed-port: 7890', 'mode: rule', 'log-level: silent',
             'ipv6: false', 'external-controller: 127.0.0.1:{port}',
             'proxies:']
    names, seen = [], set()
    for n in nodes:
        p = _clash_proxy(dict(n), meta=True)
        if not p:
            continue
        nm = str(p.get('name') or f"{p['server']}:{p['port']}")
        if nm in seen:
            i = 2
            while f'{nm} #{i}' in seen:
                i += 1
            nm = f'{nm} #{i}'
        p['name'] = nm
        seen.add(nm)
        names.append(nm)
        lines.append('  - ' + _yval(p))
    grp = {'name': GROUP, 'type': 'url-test', 'url': TEST_URL,
           'interval': 3600, 'tolerance': 50, 'proxies': names}
    lines += ['proxy-groups:', '  - ' + _yval(grp)]
    return '\n'.join(lines) + '\n', names


def _pick_port():
    import socket
    for _ in range(20):
        p = random.randint(41000, 48000)
        s = socket.socket()
        try:
            s.bind(('127.0.0.1', p))
            s.close()
            return p
        except Exception:
            continue
    return random.randint(41000, 48000)


def _wait_ready(port, proc=None, timeout=120, log=print):
    """等到 group 真正出现在 /proxies 里 (混合端口和组初始化是异步的)"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc is not None and proc.poll() is not None:
            log('[delay] mihomo 进程提前退出')
            return False
        try:
            r = requests.get(f'http://127.0.0.1:{port}/proxies/{quote(GROUP, safe="")}', timeout=2)
            if r.status_code == 200 and '"all"' in r.text:
                time.sleep(2)  # 让 url-test 组完成首次初始化
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def test_nodes(nodes, timeout_ms=5000, max_nodes=0, bin_path=None, log=print):
    """返回 {name: delay_ms|None}; 全部节点失败时返回 {}"""
    bin_path = bin_path or _find_bin()
    if not bin_path:
        log('[delay] 找不到可执行的 mihomo (设 MIHOMO_BIN 环境变量)')
        return None
    if max_nodes and len(nodes) > max_nodes:
        nodes = nodes[:max_nodes]
    workdir = tempfile.mkdtemp(prefix='delaytest_')
    if os.path.exists(GEOIP_SRC):
        try:
            shutil.copy(GEOIP_SRC, os.path.join(workdir, 'geoip.metadb'))
        except Exception:
            pass
    port = _pick_port()
    cfg, names = _cfg_yaml(nodes)
    cfg = cfg.replace('{port}', str(port))
    cf = os.path.join(workdir, 'config.yaml')
    with open(cf, 'w') as f:
        f.write(cfg)
    proc = None
    logf = None
    try:
        logf = open(os.path.join(workdir, 'mh.log'), 'w')
        proc = subprocess.Popen([bin_path, '-f', cf, '-d', workdir],
                                stdout=logf, stderr=subprocess.STDOUT)
        log(f'[delay] 启动 mihomo (port {port}, {len(names)} 节点)…')
        if not _wait_ready(port, proc=proc, timeout=120, log=log):
            try:
                log('[delay] 日志: ' + open(os.path.join(workdir, 'mh.log')).read()[-300:])
            except Exception:
                pass
            return None
        # 并发测组内全部节点
        url = (f'http://127.0.0.1:{port}/group/{quote(GROUP, safe="")}/delay'
               f'?url={quote(TEST_URL, safe="")}&timeout={timeout_ms}')
        t0 = time.time()
        r = requests.get(url, timeout=timeout_ms / 1000 + 60)
        data = r.json() if r.status_code == 200 else {}
        if not isinstance(data, dict):
            data = {}
        log(f'[delay] 测完 {len(data)} 个, {time.time() - t0:.0f}s')
        return data
    except Exception as e:
        log(f'[delay] 异常: {e}')
        return None
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
        if logf:
            logf.close()
        time.sleep(1)
        shutil.rmtree(workdir, ignore_errors=True)


def apply_result(nodes, result):
    """按测活结果过滤节点, 返回 (存活节点列表, 存活名集合)"""
    alive_names = {k for k, v in (result or {}).items() if isinstance(v, int) and v > 0}
    out = []
    for n in nodes:
        nm = n.get('name') or f"{n['server']}:{n['port']}"
        if nm in alive_names:
            out.append(n)
    return out, alive_names
