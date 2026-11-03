#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
turnscan — TURN/STUN 服务器扫描 (turn_scan.go 的 Python 等价移植)

工作流: TCPing 探活 → 协议识别(ALLOCATE) → 弱口令检测(24 组) → 中继验证
  TCP 中继: Permission + Connect + ConnectionBind + TLS 握手拿出口 IP 归属地
  UDP 中继: Allocate(UDP) + Permission + Send(DNS) + Data 回包校验

CLI:
  python3 core/turnscan.py ip_list.txt [-c 100] [-p 500] [-pt 3] [-t 5] [-ct 8]
"""
import argparse
import hashlib
import hmac
import json
import os
import random
import socket
import ssl
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

MAGIC = b'\x21\x12\xa4\x42'

ALLOC_REQ, ALLOC_OK, ALLOC_ERR = 0x0003, 0x0103, 0x0113
PERM_REQ, PERM_OK = 0x0008, 0x0108
CONN_REQ, CONN_OK = 0x000A, 0x010A
BIND_REQ, BIND_OK = 0x000B, 0x010B
SEND_IND, DATA_IND = 0x0016, 0x0017

A_USER, A_INTEGRITY, A_ERROR = 0x0006, 0x0008, 0x0009
A_REALM, A_NONCE, A_PEER, A_TRANSPORT = 0x0014, 0x0015, 0x0012, 0x0019
A_CONNID, A_SOFTWARE, A_DATA = 0x002A, 0x8022, 0x0013

VERIFY_HOST = os.environ.get('TURN_VERIFY_HOST', 'iplark.com')
VERIFY_PATH = '/ipapi/public/ipinfo?db=ipapi&lang=zh-CN'
VERIFY_PORT = int(os.environ.get('TURN_VERIFY_PORT', '443'))
FALLBACK_HOST = os.environ.get('TURN_VERIFY_HOST2', 'www.cloudflare.com')
DNS_LIST = [x.strip() for x in os.environ.get('TURN_DNS', '8.8.8.8,1.1.1.1,223.5.5.5').split(',') if x.strip()]

CREDS = [
    ('test', 'test'), ('test', 'test123'), ('test', '1234'), ('test', '123456'),
    ('coturn', 'coturn'), ('coturn', 'coturn123'), ('coturn', 'password'),
    ('guest', 'guest'), ('guest', 'guest123'),
    ('admin', 'admin'), ('admin', 'admin123'), ('admin', 'password'), ('admin', '123456'),
    ('user', 'user'), ('user', 'password'), ('user', 'user123'),
    ('turn', 'turn'), ('turn', 'turn123'), ('turn', 'password'),
    ('root', 'root'), ('root', 'password'), ('root', '123456'),
    ('username', 'password'), ('demo', 'demo'), ('webrtc', 'webrtc'),
]


# ──────────────────────────────── STUN primitives
def mk_attr(t, v):
    if isinstance(v, str):
        v = v.encode()
    pad = (4 - len(v) % 4) % 4
    return struct.pack('!HH', t, len(v)) + v + b'\x00' * pad


def mk_msg(tp, tid, *attrs):
    body = b''.join(attrs)
    return struct.pack('!HH', tp, len(body)) + MAGIC + tid + body


def sign_msg(m, key):
    if not key:
        return m
    mc = bytearray(m)
    struct.pack_into('!H', mc, 2, (struct.unpack_from('!H', mc, 2)[0] + 24) & 0xFFFF)
    mac = hmac.new(key, bytes(mc), hashlib.sha1).digest()
    return bytes(mc) + mk_attr(A_INTEGRITY, mac)


def xor_addr(ip, port):
    b = bytearray(8)
    b[1] = 0x01
    struct.pack_into('!H', b, 2, port ^ 0x2112)
    for i, part in enumerate(ip.split('.')):
        b[4 + i] = int(part) ^ MAGIC[i]
    return bytes(b)


def rand_tid():
    return bytes(random.getrandbits(8) for _ in range(12))


def parse_msg(data):
    if len(data) < 20 or data[4:8] != MAGIC:
        return None
    tp = struct.unpack_from('!H', data, 0)[0]
    ml = struct.unpack_from('!H', data, 2)[0]
    attrs, o = {}, 20
    while o + 4 <= 20 + ml and o + 4 <= len(data):
        at, al = struct.unpack_from('!HH', data, o)
        if o + 4 + al > len(data):
            break
        attrs[at] = data[o + 4:o + 4 + al]
        o += 4 + al + (4 - al % 4) % 4
    return {'tp': tp, 'attrs': attrs}


class Msg(dict):
    """轻量访问器: m.tp / m.get(a) / m.sw() / m.err_code()"""
    @property
    def tp(self):
        return self['tp']

    def get(self, k, d=None):
        return self['attrs'].get(k, d)

    def is_(self, t):
        return self['tp'] == t

    def sw(self):
        v = self['attrs'].get(A_SOFTWARE) or b''
        return v.decode('utf-8', 'ignore').strip('\x00')

    def err_code(self):
        d = self['attrs'].get(A_ERROR) or b''
        if len(d) < 4:
            return 0
        return (d[2] & 7) * 100 + d[3]


# ──────────────────────────────── 连接封装
class StunConn:
    def __init__(self, sock):
        self.sock = sock
        self.buf = b''

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass

    def _fill(self, n, deadline):
        while len(self.buf) < n:
            left = deadline - time.time()
            if left <= 0:
                raise TimeoutError('timeout')
            self.sock.settimeout(left)
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError('eof')
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def read_msg(self, deadline):
        try:
            hdr = self._fill(20, deadline)
        except Exception:
            return None
        if hdr[0] & 0xC0:
            return None
        ml = struct.unpack_from('!H', hdr, 2)[0]
        body = b''
        if ml:
            try:
                body = self._fill(ml, deadline)
            except Exception:
                return None
        r = parse_msg(hdr + body)
        return Msg(r) if r else None

    def send(self, m, timeout):
        dl = time.time() + timeout
        try:
            self.sock.settimeout(timeout)
            self.sock.sendall(m)
        except Exception:
            return None
        return self.read_msg(dl)

    def send_raw(self, m, timeout):
        try:
            self.sock.settimeout(timeout)
            self.sock.sendall(m)
            return True
        except Exception:
            return False

    def recv(self, timeout):
        return self.read_msg(time.time() + timeout)


def dial(addr, timeout):
    host, port = addr
    s = socket.create_connection((host, port), timeout=timeout)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return StunConn(s)


# ──────────────────────────────── ALLOCATE + 弱口令
def do_alloc(c, transport, known_cred='', timeout=5.0):
    tp = bytes([transport, 0, 0, 0])
    r = c.send(mk_msg(ALLOC_REQ, rand_tid(), mk_attr(A_TRANSPORT, tp)), timeout)
    if r is None:
        return None

    sw = r.sw()
    if r.is_(ALLOC_OK):
        return {'key': None, 'aa': b'', 'sw': sw, 'no_auth': True, 'cred': ''}
    if not r.is_(ALLOC_ERR) or r.err_code() != 401:
        return None

    realm = (r.get(A_REALM) or b'').decode('utf-8', 'ignore')
    nonce = r.get(A_NONCE) or b''

    try_list = CREDS
    if known_cred:
        u, _, p = known_cred.partition(':')
        try_list = [(u, p)]

    for user, passwd in try_list:
        key = hashlib.md5(f'{user}:{realm}:{passwd}'.encode()).digest()
        aa = mk_attr(A_USER, user) + mk_attr(A_REALM, realm) + mk_attr(A_NONCE, nonce)
        m = sign_msg(mk_msg(ALLOC_REQ, rand_tid(), mk_attr(A_TRANSPORT, tp), aa), key)
        r = c.send(m, timeout)
        if r is None:
            return None
        if r.is_(ALLOC_OK):
            return {'key': key, 'aa': aa, 'sw': sw or r.sw(), 'no_auth': False,
                    'cred': f'{user}:{passwd}'}
        if r.is_(ALLOC_ERR):
            if r.get(A_NONCE):
                nonce = r.get(A_NONCE)
            if r.get(A_REALM):
                realm = r.get(A_REALM).decode('utf-8', 'ignore')
            continue
        return None
    # 401 挑战存在但弱口令全败 → 是 TURN 服务器, 只是要认证
    return {'key': None, 'aa': b'', 'sw': sw, 'no_auth': False, 'cred': '',
            'need_auth': True, 'realm': realm}


def _sign(auth, m):
    return sign_msg(m, auth['key']) if auth['key'] else m


def resolve(host):
    try:
        return socket.gethostbyname(host)
    except Exception:
        return None


# ──────────────────────────────── TCP 中继验证
def test_tcp(c, addr, auth, verify_ip, timeout=5.0, conn_timeout=8.0):
    peer = mk_attr(A_PEER, xor_addr(verify_ip, VERIFY_PORT))
    perm = _sign(auth, mk_msg(PERM_REQ, rand_tid(), peer, auth['aa']))
    conn = _sign(auth, mk_msg(CONN_REQ, rand_tid(), peer, auth['aa']))

    dl = time.time() + conn_timeout
    c.sock.settimeout(conn_timeout)
    try:
        c.sock.sendall(perm + conn)
    except Exception:
        return False, ''
    r = c.read_msg(dl)
    if not r or not r.is_(PERM_OK):
        return False, ''
    r = c.recv(conn_timeout)
    if not r or not r.is_(CONN_OK) or not r.get(A_CONNID):
        return False, ''

    try:
        dc = dial(addr, timeout)
    except Exception:
        return False, ''
    try:
        bm = _sign(auth, mk_msg(BIND_REQ, rand_tid(), mk_attr(A_CONNID, r.get(A_CONNID)), auth['aa']))
        br = dc.send(bm, timeout)
        if not br or not br.is_(BIND_OK):
            return False, ''
        return _tls_probe(dc, timeout)
    finally:
        dc.close()


def _tls_probe(dc, timeout):
    """通过中继做 TLS 握手 + HTTP GET, 拿出口 IP 归属地"""
    for host, path in ((VERIFY_HOST, VERIFY_PATH), (FALLBACK_HOST, '/')):
        vip = resolve(host)
        if not vip:
            continue
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            ts = ctx.wrap_socket(dc.sock, server_hostname=host)
        except Exception:
            continue
        try:
            ts.settimeout(timeout)
            ts.sendall(f'GET {path} HTTP/1.1\r\nHost: {host}\r\n'
                       f'User-Agent: curl/8.4.0\r\nConnection: close\r\n\r\n'.encode())
            resp = b''
            while len(resp) < 8192:
                try:
                    chunk = ts.recv(4096)
                except Exception:
                    break
                if not chunk:
                    break
                resp += chunk
        except Exception:
            resp = b''
        finally:
            try:
                ts.close()
            except Exception:
                pass
        txt = resp.decode('utf-8', 'ignore')
        idx = txt.find('\r\n\r\n')
        country = ''
        if idx >= 0:
            try:
                j = json.loads(txt[idx + 4:])
                country = (j.get('country') or j.get('country_name')
                           or j.get('countryCode') or '')
                if isinstance(country, dict):
                    country = country.get('name') or country.get('code') or ''
            except Exception:
                country = ''
        if not country:
            for k in ('"country":"', '"country_name":"'):
                i = txt.find(k)
                if i >= 0:
                    country = txt[i + len(k):txt.find('"', i + len(k))][:24]
                    break
        return True, str(country)[:24]
    return False, ''


# ──────────────────────────────── UDP 中继验证
def build_dns_query():
    txid = bytes(random.getrandbits(8) for _ in range(2))
    h = bytearray(12)
    h[0:2] = txid
    h[2] = 0x01
    h[5] = 1
    q = b'\x07example\x03com\x00\x00\x01\x00\x01'
    return bytes(h) + q, txid


def check_dns(data, txid):
    if not data or len(data) < 12:
        return False
    return data[0:2] == txid and (data[2] >> 4) & 0xF == 8 and data[3] & 0xF == 0


def test_udp(addr, known_cred='', timeout=5.0, ping_timeout=3.0):
    try:
        c = dial(addr, ping_timeout)
    except Exception:
        return None, False
    try:
        auth = do_alloc(c, 17, known_cred, timeout)
        if not auth:
            return None, False
        ok = False
        for dns in DNS_LIST:
            peer = mk_attr(A_PEER, xor_addr(dns, 0))
            perm = _sign(auth, mk_msg(PERM_REQ, rand_tid(), peer, auth['aa']))
            r = c.send(perm, timeout)
            if not r or not r.is_(PERM_OK):
                continue
            q, txid = build_dns_query()
            si = mk_msg(SEND_IND, rand_tid(), mk_attr(A_PEER, xor_addr(dns, 53)), mk_attr(A_DATA, q))
            r = c.send(si, timeout)
            if r and r.is_(DATA_IND) and check_dns(r.get(A_DATA), txid):
                ok = True
                break
        return auth, ok
    finally:
        c.close()


# ──────────────────────────────── 单目标扫描
def scan_one(ip, port, ping_timeout=3.0, timeout=5.0, conn_timeout=8.0, verify_ip=None):
    addr = (ip, port)
    t0 = time.time()
    try:
        s = socket.create_connection(addr, timeout=ping_timeout)
        s.close()
    except Exception:
        return None
    rtt = int((time.time() - t0) * 1000)

    info = {'ip': ip, 'port': port, 'mode': '', 'country': '', 'auth': '',
            'cred': '', 'sw': '', 'no_auth': False, 'tcp': False, 'udp': False,
            'rtt': rtt, 'auth_required': False, 'realm': ''}

    # TCP 中继
    try:
        tc = dial(addr, timeout)
        auth = do_alloc(tc, 6, '', timeout)
        if auth:
            info['sw'] = auth['sw']
            info['no_auth'] = auth['no_auth']
            info['cred'] = auth['cred']
            if auth.get('need_auth'):
                info['auth_required'] = True
                info['realm'] = auth.get('realm', '')
            if verify_ip and not auth.get('need_auth'):
                info['tcp'], info['country'] = test_tcp(tc, addr, auth, verify_ip,
                                                        timeout, conn_timeout)
        tc.close()
    except Exception:
        pass

    # UDP 中继
    try:
        ua, udp_ok = test_udp(addr, info['cred'], timeout, ping_timeout)
        info['udp'] = udp_ok
        if ua:
            if not info['sw']:
                info['sw'] = ua['sw']
            if not info['no_auth'] and not info['cred']:
                info['no_auth'] = ua['no_auth']
                info['cred'] = ua['cred']
            if ua.get('need_auth'):
                info['auth_required'] = True
                info['realm'] = info.get('realm') or ua.get('realm', '')
    except Exception:
        pass

    if info['tcp'] and info['udp']:
        info['mode'] = 'ALL'
    elif info['tcp']:
        info['mode'] = 'TCP'
    elif info['udp']:
        info['mode'] = 'UDP'
    elif info.get('auth_required'):
        info['mode'] = 'AUTH'
    else:
        return None
    return info


# ──────────────────────────────── 批量扫描
def parse_targets(text, default_port=3478):
    out, seen = [], set()
    for line in (text or '').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        line = line.split()[0]
        if line.count(':') == 1:
            h, _, p = line.partition(':')
            if p.isdigit():
                ip, port = h, int(p)
            else:
                ip, port = line, default_port
        else:
            ip, port = line, default_port
        ip = ip.strip('[]')
        if not ip:
            continue
        key = (ip, port)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def scan(targets, concurrency=100, ping_concurrency=500, ping_timeout=3.0,
         timeout=5.0, conn_timeout=8.0, progress=None, verify_relay=True):
    """targets: [(ip,port)]; progress(done,total,alive,hits) 回调"""
    total = len(targets)
    verify_ip = resolve(VERIFY_HOST) if verify_relay else None
    alive, done, hits = 0, 0, []

    with ThreadPoolExecutor(max_workers=max(1, ping_concurrency)) as pool:
        futs = {pool.submit(_ping, ip, port, ping_timeout): (ip, port) for ip, port in targets}
        survivors = []
        for f in as_completed(futs):
            done += 1
            if f.result():
                alive += 1
                survivors.append(futs[f])
            if progress and (done % 50 == 0 or done == total):
                progress(done, total, alive, len(hits))

    done2 = 0
    identified = []
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futs = {pool.submit(scan_one, ip, port, ping_timeout, timeout,
                            conn_timeout, verify_ip): (ip, port) for ip, port in survivors}
        for f in as_completed(futs):
            done2 += 1
            r = f.result()
            if r:
                if r['mode'] == 'AUTH':
                    identified.append(r)
                else:
                    hits.append(r)
            if progress and (done2 % 5 == 0 or done2 == len(survivors)):
                progress(total, total, alive, len(hits))
    return {'total': total, 'alive': alive, 'hits': hits, 'identified': identified}


def _ping(ip, port, t):
    try:
        s = socket.create_connection((ip, port), timeout=t)
        s.close()
        return True
    except Exception:
        return False


def fmt_line(r):
    if r.get('auth_required') and not r.get('cred'):
        auth = f"NEED_AUTH(realm={r.get('realm') or '?'})"
    elif r['no_auth']:
        auth = 'NO_AUTH'
    else:
        auth = f'WEAK({r["cred"]})'
    return (f"{r['ip']}:{r['port']}  {r['mode']}  {r['country'] or '-'}  "
            f"{auth}  {r['sw'] or '?'}")


def main():
    ap = argparse.ArgumentParser(description='TURN/STUN 服务器扫描')
    ap.add_argument('targets', help='ip_list.txt 或 - (stdin)')
    ap.add_argument('-c', type=int, default=100, help='扫描并发')
    ap.add_argument('-p', type=int, default=500, help='TCPing 并发')
    ap.add_argument('-pt', type=float, default=3, help='TCPing 超时')
    ap.add_argument('-t', type=float, default=5, help='扫描超时')
    ap.add_argument('-ct', type=float, default=8, help='Connect 超时')
    ap.add_argument('--out', default='turn_results.txt')
    ap.add_argument('--no-relay', action='store_true', help='只测 Allocate, 不验证中继')
    a = ap.parse_args()

    text = sys.stdin.read() if a.targets == '-' else open(a.targets, encoding='utf-8').read()
    targets = parse_targets(text)
    if not targets:
        print('没有有效目标', file=sys.stderr)
        return 1

    t0 = time.time()

    def prog(done, total, alive, hits):
        sys.stderr.write(f'\rTURN Scan | {done}/{total} alive={alive} HIT={hits}'
                         f' {done / max(0.01, time.time() - t0):.0f}/s')
        sys.stderr.flush()

    res = scan(targets, a.c, a.p, a.pt, a.t, a.ct, prog, not a.no_relay)
    print()
    print(f"完成: {res['total']} 目标, {time.time() - t0:.1f}s")
    ident = res.get('identified') or []
    print(f"  存活: {res['alive']}, 可用中继: {len(res['hits'])}, 仅识别(需认证): {len(ident)}")
    if res['hits'] or ident:
        with open(a.out, 'w', encoding='utf-8') as f:
            f.write('# 可用中继\n')
            for r in res['hits']:
                f.write(fmt_line(r) + '\n')
            f.write('\n# 仅识别 (TURN 在, 要认证)\n')
            for r in ident:
                f.write(fmt_line(r) + '\n')
        print('结果:', a.out)
        for r in res['hits']:
            print('  [可用] ' + fmt_line(r))
        for r in ident[:30]:
            print('  [识别] ' + fmt_line(r))
    return 0


if __name__ == '__main__':
    sys.exit(main())
