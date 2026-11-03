# -*- coding: utf-8 -*-
"""
多平台配置生成: 统一节点模型 -> 各客户端格式
平台: links / v2ray(b64) / clash-mihomo / sing-box / QuantumultX / Surge / Loon / Shadowrocket
"""
import json
import base64
from urllib.parse import quote, urlencode

from .parse import b64e, _region_of

# ---------------------------------------------------------------- 节点 -> URI


def node_to_uri(n):
    t = n.get('type')
    name = quote(str(n.get('name', '')) or f"{n['server']}:{n['port']}", safe='')
    server = n['server']
    port = n['port']
    host = f'[{server}]' if ':' in server else server

    if t == 'ss':
        ui = b64e(f"{n.get('cipher','aes-128-gcm')}:{n.get('password','')}")
        extra = ''
        if n.get('plugin') == 'obfs':
            o = n.get('plugin-opts', {})
            extra = '?plugin=' + quote(
                f"obfs-local;obfs={o.get('mode','http')};obfs-host={o.get('host','')}", safe=';=')
        elif n.get('plugin') == 'v2ray-plugin':
            o = n.get('plugin-opts', {})
            p = f"v2ray-plugin;mode={o.get('mode','websocket')};host={o.get('host','')};path={o.get('path','/')}"
            if o.get('tls'):
                p += ';tls'
            extra = '?plugin=' + quote(p, safe=';=')
        return f'ss://{ui}@{host}:{port}{extra}#{name}'

    if t == 'ssr':
        pp = []
        if n.get('protocol-param'):
            pp.append('protoparam=' + b64e(n['protocol-param']))
        if n.get('obfs-param'):
            pp.append('obfsparam=' + b64e(n['obfs-param']))
        pp.append('remarks=' + b64e(n.get('name', '')))
        main = f"{server}:{port}:{n.get('protocol','origin')}:{n.get('cipher','aes-256-cfb')}:" \
               f"{n.get('obfs','plain')}:{b64e(n.get('password',''))}"
        return 'ssr://' + b64e(main + '/?' + '&'.join(pp))

    if t == 'vmess':
        assert_pwd = n.get('uuid', '')
        net = n.get('network', 'tcp')
        host_header = ''
        path = ''
        if net == 'ws':
            wo = n.get('ws-opts', {})
            path = wo.get('path', '/')
            host_header = (wo.get('headers') or {}).get('Host', '')
        elif net == 'grpc':
            path = n.get('grpc-opts', {}).get('grpc-service-name', '')
        elif net == 'h2':
            path = n.get('h2-opts', {}).get('path', '/')
            hh = n.get('h2-opts', {}).get('host', [])
            host_header = hh[0] if hh else ''
        j = {'v': '2', 'ps': n.get('name', ''), 'add': server, 'port': str(port),
             'id': assert_pwd, 'aid': str(n.get('alterId', 0)),
             'scy': n.get('cipher', 'auto') or 'auto', 'net': net,
             'type': 'none', 'host': host_header, 'path': path,
             'tls': 'tls' if n.get('tls') or n.get('reality-opts') else '',
             'sni': n.get('servername', '') or n.get('sni', ''),
             'alpn': ','.join(n.get('alpn', []) or []),
             'fp': n.get('client-fingerprint', '') or ''}
        return 'vmess://' + b64e(json.dumps(j, ensure_ascii=False))

    if t == 'vless':
        q = {'encryption': n.get('encryption', 'none') or 'none',
             'type': _net_out(n)}
        if n.get('tls'):
            q['security'] = 'reality' if n.get('reality-opts') else 'tls'
        if n.get('servername'):
            q['sni'] = n['servername']
        if n.get('flow'):
            q['flow'] = n['flow']
        if n.get('reality-opts'):
            q['pbk'] = n['reality-opts'].get('public-key', '')
            q['sid'] = n['reality-opts'].get('short-id', '')
            if n['reality-opts'].get('spider-x'):
                q['spx'] = n['reality-opts']['spider-x']
        if n.get('client-fingerprint'):
            q['fp'] = n['client-fingerprint']
        if n.get('skip-cert-verify'):
            q['allowInsecure'] = '1'
        if n.get('alpn'):
            q['alpn'] = ','.join(n['alpn'])
        hh, path = _ws_host_path(n)
        if hh:
            q['host'] = hh
        if path:
            q['path'] = path
        if n.get('grpc-opts'):
            q['serviceName'] = n['grpc-opts'].get('grpc-service-name', '')
        return f"vless://{n.get('uuid','')}@{host}:{port}?{urlencode(q)}#{name}"

    if t == 'trojan':
        q = {}
        sec = 'tls'
        if n.get('network') and n['network'] != 'tcp':
            q['type'] = _net_out(n)
        if n.get('sni'):
            q['sni'] = n['sni']
        if n.get('skip-cert-verify'):
            q['allowInsecure'] = '1'
        if n.get('alpn'):
            q['alpn'] = ','.join(n['alpn'])
        hh, path = _ws_host_path(n)
        if hh:
            q['host'] = hh
        if path:
            q['path'] = path
        qs = ('?' + urlencode(q)) if q else ''
        return f"trojan://{quote(str(n.get('password','')), safe='')}@{host}:{port}{qs}#{name}"

    if t == 'hysteria2':
        q = {}
        if n.get('sni'):
            q['sni'] = n['sni']
        if n.get('skip-cert-verify'):
            q['insecure'] = '1'
        if n.get('obfs'):
            q['obfs'] = n['obfs']
            q['obfs-password'] = n.get('obfs-password', '')
        if n.get('alpn'):
            q['alpn'] = ','.join(n['alpn'])
        qs = ('?' + urlencode(q)) if q else ''
        return f"hysteria2://{quote(str(n.get('password','')), safe='')}@{host}:{port}{qs}#{name}"

    if t == 'hysteria':
        q = {}
        if n.get('protocol') and n['protocol'] != 'udp':
            q['protocol'] = str(n['protocol']).lower()
        if n.get('auth-str'):
            q['auth'] = n['auth-str']
        if n.get('up'):
            q['upmbps'] = n['up']
        if n.get('down'):
            q['downmbps'] = n['down']
        if n.get('obfs'):
            q['obfs'] = n['obfs']
        if n.get('sni'):
            q['peer'] = n['sni']
        if n.get('skip-cert-verify'):
            q['insecure'] = '1'
        return f"hysteria://{host}:{port}?{urlencode(q)}#{name}"

    if t == 'tuic':
        q = {}
        if n.get('congestion-controller'):
            q['congestion_control'] = n['congestion-controller']
        if n.get('udp-relay-mode'):
            q['udp_relay_mode'] = n['udp-relay-mode']
        if n.get('sni'):
            q['sni'] = n['sni']
        if n.get('skip-cert-verify'):
            q['allow_insecure'] = '1'
        if n.get('alpn'):
            q['alpn'] = ','.join(n['alpn'])
        qs = ('?' + urlencode(q)) if q else ''
        return (f"tuic://{quote(str(n.get('uuid','')), safe='')}:"
                f"{quote(str(n.get('password','')), safe='')}@{host}:{port}{qs}#{name}")

    if t == 'anytls':
        q = {}
        if n.get('sni'):
            q['sni'] = n['sni']
        if n.get('skip-cert-verify'):
            q['insecure'] = '1'
        qs = ('?' + urlencode(q)) if q else ''
        return f"anytls://{quote(str(n.get('password','')), safe='')}@{host}:{port}{qs}#{name}"

    if t in ('http', 'socks5'):
        scheme = 'socks5' if t == 'socks5' else ('https' if n.get('tls') else 'http')
        auth = ''
        if n.get('username'):
            auth = quote(str(n['username']), safe='')
            if n.get('password'):
                auth += ':' + quote(str(n['password']), safe='')
            auth += '@'
        return f'{scheme}://{auth}{host}:{port}#{name}'

    if t == 'snell':
        q = {}
        if n.get('version'):
            q['version'] = n['version']
        o = n.get('obfs-opts') or {}
        if o.get('mode'):
            q['obfs'] = o['mode']
            if o.get('host'):
                q['host'] = o['host']
        qs = ('?' + urlencode(q)) if q else ''
        return f"snell://{quote(str(n.get('psk','')), safe='')}@{host}:{port}{qs}#{name}"

    if t == 'ssh':
        auth = quote(str(n.get('username', 'root')), safe='')
        if n.get('password'):
            auth += ':' + quote(str(n['password']), safe='')
        return f'ssh://{auth}@{host}:{port}#{name}'

    if t == 'wireguard':
        q = {}
        if n.get('public-key'):
            q['publickey'] = n['public-key']
        if n.get('ip'):
            q['address'] = n['ip']
        if n.get('ipv6'):
            q['ipv6'] = n['ipv6']
        if n.get('mtu'):
            q['mtu'] = n['mtu']
        if n.get('reserved'):
            q['reserved'] = ','.join(str(x) for x in n['reserved'])
        qs = ('?' + urlencode(q)) if q else ''
        return f"wireguard://{quote(str(n.get('private-key','')), safe='')}@{host}:{port}{qs}#{name}"
    return None


def _net_out(n):
    net = n.get('network', 'tcp')
    return {'tcp': 'tcp', 'ws': 'ws', 'grpc': 'grpc', 'h2': 'h2',
            'http': 'http', 'quic': 'quic'}.get(net, 'tcp')


def _ws_host_path(n):
    net = n.get('network', 'tcp')
    if net == 'ws':
        wo = n.get('ws-opts') or {}
        return (wo.get('headers') or {}).get('Host', ''), wo.get('path', '')
    if net == 'grpc':
        return '', (n.get('grpc-opts') or {}).get('grpc-service-name', '')
    if net == 'h2':
        ho = n.get('h2-opts') or {}
        hh = ho.get('host') or []
        return (hh[0] if hh else ''), ho.get('path', '')
    if net == 'http':
        ho = n.get('http-opts') or {}
        hh = (ho.get('headers') or {}).get('Host') or []
        pp = ho.get('path') or ['/']
        return (hh[0] if hh else ''), (pp[0] if pp else '/')
    return '', ''

# ---------------------------------------------------------------- YAML 序列化(可控, 不依赖 C 版行为)
_YAML_SPECIAL = set(':#{}[],&*?|-<>=!%@`"\'\n\t\\')
_NUM_RE = None
_YAML_KW = {'true', 'false', 'yes', 'no', 'on', 'off', 'null', '~', 'none'}


def _yval(v):
    global _NUM_RE
    if v is None:
        return 'null'
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        if _NUM_RE is None:
            import re as _re
            _NUM_RE = _re.compile(r'^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$')
        need = (v == '' or any(c in _YAML_SPECIAL for c in v) or v != v.strip()
                or _NUM_RE.match(v) or v.lower() in _YAML_KW)
        if need:
            return "'" + v.replace("'", "''") + "'"
        return v
    if isinstance(v, list):
        return '[' + ', '.join(_yval(x) for x in v) + ']'
    if isinstance(v, dict):
        return '{' + ', '.join(f'{k}: {_yval(x)}' for k, x in v.items()) + '}'
    return _yval(str(v))


def _clean(d):
    """递归清掉 None / 空值"""
    if isinstance(d, dict):
        out = {}
        for k, v in d.items():
            v = _clean(v)
            if v is None or v == '' or v == {} or v == []:
                continue
            out[k] = v
        return out
    if isinstance(d, list):
        return [_clean(x) for x in d]
    return d


META_ONLY = {'vless', 'hysteria', 'hysteria2', 'tuic', 'anytls', 'wireguard', 'ssh', 'mieru', 'juicity'}


def _clash_proxy(n, meta=True):
    p = dict(n)
    p.pop('encryption', None)
    ro = p.get('reality-opts')
    if isinstance(ro, dict):
        ro.pop('spider-x', None)
    if p.get('type') == 'vmess' and not p.get('cipher'):
        p['cipher'] = 'auto'
    if not p.get('name'):
        p['name'] = f"{p.get('server', '')}:{p.get('port', '')}"
    # mihomo 强校验: hysteria v1 必须有 up/down
    if p.get('type') == 'hysteria':
        if not p.get('up'):
            p['up'] = '50 Mbps'
        if not p.get('down'):
            p['down'] = '100 Mbps'
        if not p.get('protocol'):
            p['protocol'] = 'udp'
        if not p.get('auth-str') and not p.get('auth'):
            p['auth-str'] = p.get('password', '') or ''
    # vless flow 只对裸 tcp 有效, 无效组合会被 mihomo 拒绝
    if p.get('type') == 'vless' and p.get('flow') and p.get('network', 'tcp') != 'tcp':
        p.pop('flow', None)
    if not meta and p.get('type') in META_ONLY:
        return None
    return _clean(p)


def to_clash(nodes, meta=True, proxy_groups=True):
    """输出 mihomo/clash 完整配置"""
    lines = ['# Generated by tgsubbot', 'mixed-port: 7890', 'allow-lan: false',
             'mode: rule', 'log-level: info', "external-controller: '127.0.0.1:9090'",
             'ipv6: true',
             'dns:', '  enable: true', '  enhanced-mode: fake-ip',
             '  fake-ip-range: 198.18.0.1/16', '  nameserver:',
             "    - 'https://223.5.5.5/dns-query'", "    - 'https://1.1.1.1/dns-query'",
             'proxies:']
    valid, names = [], []
    seen = set()
    for n in nodes:
        p = _clash_proxy(n, meta)
        if not p:
            continue
        nm = str(p.get('name', '') or f"{p['server']}:{p['port']}")
        if nm in seen:
            i = 2
            while f'{nm} #{i}' in seen:
                i += 1
            nm = f'{nm} #{i}'
        p['name'] = nm
        seen.add(nm)
        valid.append(p)
        names.append(nm)
        lines.append('  - ' + _yval(p))
    if proxy_groups and valid:
        lines += ['proxy-groups:',
                  "  - {name: '🚀 节点选择', type: select, proxies: ['♻️ 自动选择', 'DIRECT']}",
                  "  - {name: '♻️ 自动选择', type: url-test, url: 'http://www.gstatic.com/generate_204', interval: 300, tolerance: 50, proxies: []}",
                  "  - {name: '🌍 国外媒体', type: select, proxies: ['🚀 节点选择', '♻️ 自动选择', 'DIRECT']}",
                  "  - {name: '🎯 全球直连', type: select, proxies: ['DIRECT', '🚀 节点选择']}"]
        # 把节点名挂到组里(可控 YAML 长度, 直接拼接)
        idx = '  - ' + _yval({'name': '🚀 节点选择', 'type': 'select',
                              'proxies': ['♻️ 自动选择', 'DIRECT'] + names})
        lines[lines.index("  - {name: '🚀 节点选择', type: select, proxies: ['♻️ 自动选择', 'DIRECT']}")] = idx
        idx2 = '  - ' + _yval({'name': '♻️ 自动选择', 'type': 'url-test',
                               'url': 'http://www.gstatic.com/generate_204',
                               'interval': 300, 'tolerance': 50, 'proxies': names})
        lines[lines.index("  - {name: '♻️ 自动选择', type: url-test, url: 'http://www.gstatic.com/generate_204', interval: 300, tolerance: 50, proxies: []}")] = idx2
        lines += ['rules:',
                  "  - 'GEOIP,CN,🎯 全球直连'",
                  "  - 'MATCH,🚀 节点选择'"]
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------- v2ray b64 / links
def to_links(nodes):
    return '\n'.join(x for x in (node_to_uri(n) for n in nodes) if x) + '\n'


def to_v2ray_b64(nodes):
    txt = to_links(nodes)
    return base64.b64encode(txt.encode()).decode()


# ---------------------------------------------------------------- sing-box
def _sb_node(n):
    t = n.get('type')
    out = {'tag': n.get('name', '') or f"{n['server']}:{n['port']}",
           'server': n['server'], 'server_port': n['port']}
    tls = {}
    if n.get('tls') or n.get('reality-opts') or t in ('hysteria2', 'tuic', 'anytls', 'trojan'):
        tls = {'enabled': True}
        sni = n.get('servername') or n.get('sni') or ''
        if sni:
            tls['server_name'] = sni
        if n.get('skip-cert-verify'):
            tls['insecure'] = True
        if n.get('alpn'):
            tls['alpn'] = n['alpn']
        if n.get('client-fingerprint'):
            tls['utls'] = {'enabled': True, 'fingerprint': n['client-fingerprint']}
        if n.get('reality-opts'):
            tls['reality'] = {'enabled': True,
                              'public_key': n['reality-opts'].get('public-key', ''),
                              'short_id': n['reality-opts'].get('short-id', '')}
    tr = None
    net = n.get('network', 'tcp')
    if net == 'ws':
        wo = n.get('ws-opts') or {}
        tr = {'type': 'ws', 'path': wo.get('path', '/')}
        h = (wo.get('headers') or {}).get('Host')
        if h:
            tr['headers'] = {'Host': h}
    elif net == 'grpc':
        tr = {'type': 'grpc',
              'service_name': (n.get('grpc-opts') or {}).get('grpc-service-name', '')}
    if t == 'ss':
        out.update({'type': 'shadowsocks', 'method': n.get('cipher', ''),
                    'password': n.get('password', '')})
        if n.get('plugin') == 'obfs':
            o = n.get('plugin-opts', {})
            out['plugin'] = 'obfs-local'
            out['plugin_opts'] = f"obfs={o.get('mode','http')};obfs-host={o.get('host','')}"
        elif n.get('plugin') == 'v2ray-plugin':
            o = n.get('plugin-opts', {})
            out['plugin'] = 'v2ray-plugin'
            out['plugin_opts'] = f"mode={o.get('mode','websocket')};host={o.get('host','')};path={o.get('path','/')}"
    elif t == 'vmess':
        out.update({'type': 'vmess', 'uuid': n.get('uuid', ''),
                    'security': n.get('cipher', 'auto') or 'auto',
                    'alter_id': n.get('alterId', 0)})
        if tr:
            out['transport'] = tr
    elif t == 'vless':
        out.update({'type': 'vless', 'uuid': n.get('uuid', ''),
                    'packet_encoding': 'xudp'})
        if n.get('flow'):
            out['flow'] = n['flow']
        if tr:
            out['transport'] = tr
    elif t == 'trojan':
        out.update({'type': 'trojan', 'password': n.get('password', '')})
        if tr:
            out['transport'] = tr
    elif t == 'hysteria2':
        out.update({'type': 'hysteria2', 'password': n.get('password', '')})
        if n.get('obfs'):
            out['obfs'] = {'type': n['obfs'],
                           'password': n.get('obfs-password', '')}
    elif t == 'tuic':
        out.update({'type': 'tuic', 'uuid': n.get('uuid', ''),
                    'password': n.get('password', ''),
                    'congestion_control': n.get('congestion-controller', 'bbr') or 'bbr',
                    'udp_relay_mode': n.get('udp-relay-mode', 'native') or 'native'})
    elif t == 'anytls':
        out.update({'type': 'anytls', 'password': n.get('password', '')})
    elif t == 'http':
        out.update({'type': 'http', 'version': '1.1'})
        if n.get('username'):
            out['username'] = n['username']
        if n.get('password'):
            out['password'] = n['password']
    elif t == 'socks5':
        out.update({'type': 'socks', 'version': '5'})
        if n.get('username'):
            out['username'] = n['username']
        if n.get('password'):
            out['password'] = n['password']
    else:
        return None
    if tls:
        out['tls'] = tls
    return out


def to_singbox(nodes, full_config=True):
    outs = []
    names = []
    for n in nodes:
        o = _sb_node(n)
        if o:
            outs.append(o)
            names.append(o['tag'])
    if not outs:
        return json.dumps({'outbounds': []}, ensure_ascii=False, indent=2)
    if not full_config:
        return json.dumps({'outbounds': outs}, ensure_ascii=False, indent=2)
    cfg = {
        'log': {'level': 'info', 'timestamp': True},
        'dns': {'servers': [{'tag': 'ali', 'address': '223.5.5.5'},
                            {'tag': 'cf', 'address': '1.1.1.1', 'detour': '🚀 节点选择'}],
                'strategy': 'prefer_ipv4'},
        'inbounds': [{'type': 'mixed', 'tag': 'mixed-in',
                      'listen': '127.0.0.1', 'listen_port': 2080,
                      'sniff': True, 'sniff_override_destination': False}],
        'outbounds': [{'type': 'selector', 'tag': '🚀 节点选择',
                       'outbounds': ['♻️ 自动选择', 'direct'] + names},
                      {'type': 'urltest', 'tag': '♻️ 自动选择',
                       'outbounds': names,
                       'url': 'http://www.gstatic.com/generate_204',
                       'interval': '5m', 'tolerance': 50}] + outs +
                     [{'type': 'direct', 'tag': 'direct'}],
        'route': {'rules': [{'action': 'sniff'}, {'protocol': 'dns', 'action': 'hijack-dns'}],
                  'final': '🚀 节点选择',
                  'auto_detect_interface': True},
    }
    return json.dumps(cfg, ensure_ascii=False, indent=2)

# ---------------------------------------------------------------- 其他平台 (返回 (文本, 被跳过的类型统计))
def _san(n):
    return str(n.get('name', '') or f"{n['server']}:{n['port']}").replace(',', ' ')


def to_quantumultx(nodes):
    """Quantumult X 片段"""
    lines, skipped, used = [], {}, 0
    for n in nodes:
        t, s, p = n.get('type'), n['server'], n['port']
        nm = _san(n)
        try:
            if t == 'ss':
                seg = [f'shadowsocks={s}:{p}', f"method={n.get('cipher','')}",
                       f"password={n.get('password','')}"]
                if n.get('plugin') == 'obfs':
                    o = n.get('plugin-opts', {})
                    seg.append('obfs=' + o.get('mode', 'http'))
                    if o.get('host'):
                        seg.append('obfs-host=' + o['host'])
                seg += ['fast-open=false', 'udp-relay=true', f'tag={nm}']
            elif t == 'vmess':
                seg = [f'vmess={s}:{p}', 'method=none', f"password={n.get('uuid','')}"]
                net = n.get('network', 'tcp')
                if net == 'ws':
                    wo = n.get('ws-opts') or {}
                    seg.append('obfs=wss' if n.get('tls') else 'obfs=ws')
                    h = (wo.get('headers') or {}).get('Host')
                    if h:
                        seg.append('obfs-host=' + h)
                    seg.append('obfs-uri=' + wo.get('path', '/'))
                elif n.get('tls'):
                    seg.append('obfs=over-tls')
                    if n.get('servername'):
                        seg.append('tls-host=' + n['servername'])
                seg += ['tls-verification=' + ('false' if n.get('skip-cert-verify') else 'true'),
                        'fast-open=false', 'udp-relay=true', f'tag={nm}']
            elif t == 'trojan':
                seg = [f'trojan={s}:{p}', f"password={n.get('password','')}",
                       'over-tls=true']
                sni = n.get('sni') or n.get('servername')
                if sni:
                    seg.append('tls-host=' + sni)
                seg.append('tls-verification=' + ('false' if n.get('skip-cert-verify') else 'true'))
                seg += ['fast-open=false', 'udp-relay=true', f'tag={nm}']
            elif t == 'vless':
                seg = [f'vless={s}:{p}', 'method=none', f"password={n.get('uuid','')}"]
                if n.get('tls'):
                    seg.append('obfs=over-tls')
                    if n.get('servername'):
                        seg.append('tls-host=' + n['servername'])
                seg += ['tls-verification=' + ('false' if n.get('skip-cert-verify') else 'true'),
                        'fast-open=false', 'udp-relay=true', f'tag={nm}']
            elif t in ('http', 'socks5'):
                seg = [f'{"http" if t == "http" else "socks5"}={s}:{p}']
                if n.get('username'):
                    seg.append('username=' + n['username'])
                if n.get('password'):
                    seg.append('password=' + n['password'])
                seg += ['fast-open=false', 'udp-relay=true', f'tag={nm}']
            else:
                skipped[t] = skipped.get(t, 0) + 1
                continue
        except Exception:
            skipped[t] = skipped.get(t, 0) + 1
            continue
        lines.append(', '.join(seg))
        used += 1
    head = '# QuantumultX 节点 (由 tgsubbot 生成)'
    return head + '\n' + '\n'.join(lines) + '\n', skipped


def to_surge(nodes):
    """Surge 策略行"""
    lines, skipped, used = [], {}, 0
    for n in nodes:
        t, s, p = n.get('type'), n['server'], n['port']
        nm = _san(n)
        if t == 'ss':
            seg = [f'{nm} = ss, {s}, {p}', f"encrypt-method={n.get('cipher','')}",
                   f"password={n.get('password','')}", 'udp-relay=true']
            if n.get('plugin') == 'obfs':
                o = n.get('plugin-opts', {})
                seg.append('obfs=' + o.get('mode', 'http'))
                seg.append('obfs-host=' + o.get('host', 'www.bing.com'))
        elif t == 'vmess':
            seg = [f'{nm} = vmess, {s}, {p}', f"username={n.get('uuid','')}"]
            if n.get('network') == 'ws':
                wo = n.get('ws-opts') or {}
                seg.append('ws=true')
                seg.append('ws-path=' + wo.get('path', '/'))
                h = (wo.get('headers') or {}).get('Host')
                if h:
                    seg.append('ws-headers=Host:' + h)
            if n.get('tls'):
                seg.append('tls=true')
                if n.get('servername'):
                    seg.append('sni=' + n['servername'])
            if n.get('skip-cert-verify'):
                seg.append('skip-cert-verify=true')
        elif t == 'trojan':
            seg = [f'{nm} = trojan, {s}, {p}', f"password={n.get('password','')}"]
            if n.get('sni'):
                seg.append('sni=' + n['sni'])
            if n.get('skip-cert-verify'):
                seg.append('skip-cert-verify=true')
            if n.get('network') == 'ws':
                wo = n.get('ws-opts') or {}
                seg.append('ws=true')
                seg.append('ws-path=' + wo.get('path', '/'))
        elif t == 'hysteria2':
            seg = [f'{nm} = hysteria2, {s}, {p}', f"password={n.get('password','')}"]
            if n.get('sni'):
                seg.append('sni=' + n['sni'])
            if n.get('skip-cert-verify'):
                seg.append('skip-cert-verify=true')
            if n.get('down'):
                seg.append(f'download-bandwidth={n["down"]}')
        elif t == 'snell':
            seg = [f'{nm} = snell, {s}, {p}', f"psk={n.get('psk','')}",
                   f"version={n.get('version', 3)}"]
        elif t == 'http':
            seg = [f'{nm} = http, {s}, {p}']
            if n.get('tls'):
                seg.append('tls=true')
            if n.get('username'):
                seg.append('username=' + n['username'])
            if n.get('password'):
                seg.append('password=' + n['password'])
        else:
            skipped[t] = skipped.get(t, 0) + 1
            continue
        lines.append(', '.join(seg))
        used += 1
    return ('# Surge 节点 (由 tgsubbot 生成; 粘贴到 [Proxy] 段)\n'
            + '\n'.join(lines) + '\n'), skipped


def to_loon(nodes):
    """Loon 节点行"""
    lines, skipped, used = [], {}, 0
    for n in nodes:
        t, s, p = n.get('type'), n['server'], n['port']
        nm = _san(n)
        if t == 'ss':
            seg = [f'{nm} = Shadowsocks, {s}, {p}, {n.get("cipher","")}, "{n.get("password","")}"']
        elif t == 'vmess':
            seg = [f'{nm} = VMess, {s}, {p}, none, "{n.get("uuid","")}"']
        elif t == 'vless':
            seg = [f'{nm} = VLESS, {s}, {p}, "{n.get("uuid","")}"']
        elif t == 'trojan':
            seg = [f'{nm} = Trojan, {s}, {p}, "{n.get("password","")}"']
        elif t == 'hysteria2':
            seg = [f'{nm} = Hysteria2, {s}, {p}, "{n.get("password","")}"']
        elif t == 'tuic':
            seg = [f'{nm} = TUIC, {s}, {p}, "{n.get("uuid","")}", "{n.get("password","")}"']
        else:
            skipped[t] = skipped.get(t, 0) + 1
            continue
        net = n.get('network', 'tcp')
        if net == 'ws' and t in ('vmess', 'vless', 'trojan'):
            wo = n.get('ws-opts') or {}
            seg.append('transport:ws')
            seg.append('path:' + wo.get('path', '/'))
            h = (wo.get('headers') or {}).get('Host')
            if h:
                seg.append('host:' + h)
        sni = n.get('servername') or n.get('sni')
        if n.get('tls') or sni:
            seg.append('tls:true')
            if sni:
                seg.append('tls-name:' + sni)
            if n.get('skip-cert-verify'):
                seg.append('skip-cert-verify:true')
        lines.append(', '.join(seg))
        used += 1
    return ('# Loon 节点 (由 tgsubbot 生成)\n' + '\n'.join(lines) + '\n'), skipped


PLATFORMS = {
    'links': '📄 链接列表 (txt)',
    'v2ray': '🅥 V2Ray / Shadowrocket 订阅 (base64)',
    'clash': '☂️ Clash / Mihomo (yaml)',
    'singbox': '📦 Sing-box (json)',
    'qx': '🦋 Quantumult X',
    'surge': '🌊 Surge',
    'loon': '🎈 Loon',
    'clash_classic': '☂️ Clash 经典版 (过滤 Meta 协议)',
}
