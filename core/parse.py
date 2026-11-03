# -*- coding: utf-8 -*-
"""
统一节点模型 + 订阅解析
内部模型刻意采用 mihomo(Clash.Meta) 字段命名, 方便直接输出配置,
生成其他平台时再做字段转换。

支持解析:
  URI: ss / ssr / vmess / vless / trojan / trojan-go / hysteria / hysteria2(hy2)
       / tuic / anytls / wireguard / http(s) / socks5 / snell / ssh
  订阅: base64 链接列表 / 明文链接列表 / mihomo-clash YAML / sing-box JSON
"""
import base64
import json
import re
from urllib.parse import urlparse, parse_qs, unquote

# ---------------------------------------------------------------- 工具


def b64d(s):
    if not s:
        return b''
    s = re.sub(r'\s+', '', str(s)).replace('-', '+').replace('_', '/')
    s += '=' * (-len(s) % 4)
    try:
        return base64.b64decode(s)
    except Exception:
        return b''


def b64d_str(s, enc='utf-8'):
    try:
        return b64d(s).decode(enc, 'ignore')
    except Exception:
        return ''


def b64e(s):
    if isinstance(s, str):
        s = s.encode()
    return base64.b64encode(s).decode()


def _i(v, d=0):
    try:
        return int(str(v).strip())
    except Exception:
        return d


def _b(v):
    if isinstance(v, bool):
        return v
    return str(v).lower() in ('1', 'true', 'yes', 'on')


def _first(v):
    if isinstance(v, list):
        return v[0] if v else ''
    return v if v is not None else ''


def _split_hostport(hp):
    """解析 host:port, 兼容 [v6]:port"""
    hp = hp.strip()
    if hp.startswith('['):
        m = re.match(r'\[(.+)\]:(\d+)', hp)
        if m:
            return m.group(1), _i(m.group(2))
        return hp, 0
    if hp.count(':') >= 2 and ']' not in hp:  # 裸 IPv6 无端口
        return hp, 0
    if ':' in hp:
        h, _, p = hp.rpartition(':')
        return h, _i(p)
    return hp, 0


def _q(params, *names, default=''):
    """从 parse_qs 结果取第一个命中的参数"""
    for n in names:
        if n in params:
            return _first(params[n])
    return default


def _strip_name(s):
    return unquote(s).strip() if s else ''


# ---------------------------------------------------------------- 各协议 URI 解析
def parse_ss(uri, name=''):
    """SIP002 或 旧版整段 base64; 支持 plugin 参数"""
    body = uri[len('ss://'):]
    frag = ''
    if '#' in body:
        body, frag = body.split('#', 1)
        frag = _strip_name(frag)
    name = frag or name
    query = ''
    if '?' in body:
        body, query = body.split('?', 1)
    params = parse_qs(query)
    plugin = _q(params, 'plugin')
    # 形式 A: ss://base64(method:pass)@host:port
    if '@' in body:
        userinfo, hostport = body.rsplit('@', 1)
        try:
            ui = b64d_str(userinfo)
            if ':' not in ui:
                ui = unquote(userinfo)
        except Exception:
            ui = unquote(userinfo)
        method, _, password = ui.partition(':')
    else:
        # 形式 B: 整段 base64(method:pass@host:port)
        dec = b64d_str(body)
        if '@' in dec:
            ui, _, hostport = dec.rpartition('@')
            method, _, password = ui.partition(':')
        else:
            return None
    server, port = _split_hostport(hostport)
    if not server or not port:
        return None
    node = {'type': 'ss', 'name': name or f'{server}:{port}',
            'server': server, 'port': port,
            'cipher': method, 'password': password}
    if plugin:
        p = plugin.split(';')
        pname = p[0]
        popts = {}
        for kv in p[1:]:
            if '=' in kv:
                k, _, v = kv.partition('=')
                popts[k] = v
            else:
                popts[kv] = 'true'
        if pname in ('obfs-local', 'simple-obfs', 'obfs'):
            node['plugin'] = 'obfs'
            node['plugin-opts'] = {'mode': popts.get('obfs', 'http'),
                                   'host': popts.get('obfs-host', '')}
        elif 'v2ray' in pname:
            node['plugin'] = 'v2ray-plugin'
            node['plugin-opts'] = {'mode': popts.get('mode', 'websocket'),
                                   'host': popts.get('host', ''),
                                   'path': popts.get('path', '/'),
                                   'tls': 'tls' in plugin}
            if node['plugin-opts'].get('tls') and popts.get('host'):
                pass
        elif 'shadow-tls' in pname:
            node['plugin'] = 'shadow-tls'
            node['plugin-opts'] = {'host': popts.get('host', ''),
                                   'password': popts.get('password', ''),
                                   'version': _i(popts.get('version', '3'), 3)}
    return node


def try_b64_text(s):
    """尝试 base64 解码为可读文本, 失败返回 ''"""
    if not s:
        return ''
    t = re.sub(r'\s+', '', str(s)).replace('-', '+').replace('_', '/')
    t += '=' * (-len(t) % 4)
    try:
        raw = base64.b64decode(t)
        txt = raw.decode('utf-8')
        if txt and all(c.isprintable() or c == ' ' for c in txt):
            return txt
    except Exception:
        pass
    return ''


def parse_ssr(uri):
    """ssr://base64(host:port:proto:method:obfs:base64pass/?params)"""
    dec = b64d_str(uri[len('ssr://'):])
    if not dec or ':' not in dec:
        return None
    main, _, query = dec.partition('/?')
    parts = main.split(':')
    if len(parts) >= 6:
        b64pass = parts[5]
        host = parts[0]
        port = _i(parts[1])
        protocol = parts[2]
        method = parts[3]
        obfs = parts[4]
    elif len(parts) == 5:
        # 某些变体: host:port:proto:method:obfs 且 pass 在 query
        host, port, protocol, method, obfs = parts[0], _i(parts[1]), parts[2], parts[3], parts[4]
        b64pass = ''
    else:
        return None
    params = parse_qs(query) if query else {}
    password = b64d_str(b64pass)
    rem = _q(params, 'remarks')
    name = try_b64_text(rem) or rem or f'{host}:{port}'
    node = {'type': 'ssr', 'name': name,
            'server': host, 'port': port, 'cipher': method, 'password': password,
            'protocol': protocol, 'obfs': obfs}
    if _q(params, 'protoparam'):
        node['protocol-param'] = try_b64_text(_q(params, 'protoparam')) or ''
    if _q(params, 'obfsparam'):
        node['obfs-param'] = try_b64_text(_q(params, 'obfsparam')) or ''
    return node


VMESS_NET_MAP = {'tcp': 'tcp', 'ws': 'ws', 'h2': 'h2', 'http': 'http',
                 'grpc': 'grpc', 'quic': 'quic', 'kcp': 'tcp', 'xhttp': 'ws'}


def parse_vmess(uri, name=''):
    body = uri[len('vmess://'):].strip()
    # v2rayN: base64(JSON)
    dec = b64d_str(body)
    if dec.strip().startswith('{'):
        try:
            j = json.loads(dec)
        except Exception:
            return None
        net = str(j.get('net', 'tcp')).lower()
        tls_sec = str(j.get('tls', '')).lower()
        security = str(j.get('scy', j.get('security', 'auto')) or 'auto')
        node = {'type': 'vmess', 'name': str(j.get('ps', name) or name),
                'server': str(j.get('add', '')), 'port': _i(j.get('port')),
                'uuid': str(j.get('id', '')), 'alterId': _i(j.get('aid', 0)),
                'cipher': security if security else 'auto',
                'network': VMESS_NET_MAP.get(net, net)}
        if tls_sec and tls_sec not in ('none', ''):
            node['tls'] = True
            if tls_sec == 'reality':
                node['reality-opts'] = {'public-key': str(j.get('pbk', '')),
                                        'short-id': str(j.get('sid', ''))}
        sni = str(j.get('sni', '') or '')
        if sni:
            node['servername'] = sni
        fp = str(j.get('fp', '') or '')
        if fp:
            node['client-fingerprint'] = fp
        alpn = j.get('alpn', '')
        if alpn:
            node['alpn'] = [x for x in str(alpn).split(',') if x]
        host = str(j.get('host', '') or '')
        path = str(j.get('path', '') or '')
        if node['network'] == 'ws':
            node['ws-opts'] = {'path': path or '/',
                               'headers': {'Host': host} if host else {}}
        elif node['network'] == 'grpc':
            node['grpc-opts'] = {'grpc-service-name': path}
        elif node['network'] == 'h2':
            node['h2-opts'] = {'path': path or '/',
                               'host': [host] if host else []}
        elif node['network'] == 'http':
            node['http-opts'] = {'path': [path or '/'],
                                 'headers': {'Host': [host]} if host else {}}
        if not node['server'] or not node['port']:
            return None
        return node
    # 标准 vmess://uuid@host:port?params (AEAD)
    if '@' in body:
        try:
            u = urlparse('vmess://' + body)
            uuid = u.username or ''
            pw = u.password or ''
            node = {'type': 'vmess', 'name': _strip_name(u.fragment) or name,
                    'server': u.hostname or '', 'port': u.port or 0,
                    'uuid': uuid, 'alterId': 0, 'cipher': 'auto',
                    'network': 'tcp'}
            params = parse_qs(u.query)
            node['network'] = VMESS_NET_MAP.get(_q(params, 'type', 'net', 'obfs'), 'tcp')
            sec = _q(params, 'security', 'tls')
            if sec and sec not in ('none', ''):
                node['tls'] = True
            sni = _q(params, 'sni', 'peer')
            if sni:
                node['servername'] = sni
            host = _q(params, 'host')
            path = _q(params, 'path')
            if node['network'] == 'ws':
                node['ws-opts'] = {'path': path or '/',
                                   'headers': {'Host': host} if host else {}}
            elif node['network'] == 'grpc':
                node['grpc-opts'] = {'grpc-service-name': _q(params, 'serviceName', 'path')}
            if pw:
                node['password'] = pw
            if not node['server'] or not node['port']:
                return None
            return node
        except Exception:
            return None
    return None


def parse_vless(uri, name=''):
    u = urlparse(uri)
    if not u.hostname:
        return None
    params = parse_qs(u.query)
    node = {'type': 'vless', 'name': _strip_name(u.fragment) or name,
            'server': u.hostname, 'port': u.port or 0, 'uuid': u.username or ''}
    flow = _q(params, 'flow')
    if flow:
        node['flow'] = flow
    enc = _q(params, 'encryption')
    if enc and enc != 'none':
        node['encryption'] = enc
    sec = _q(params, 'security').lower()
    if sec in ('tls', 'reality'):
        node['tls'] = True
    if sec == 'reality':
        node['reality-opts'] = {'public-key': _q(params, 'pbk', 'publicKey'),
                                'short-id': _q(params, 'sid', 'shortId')}
        spx = _q(params, 'spx')
        if spx:
            node['reality-opts']['spider-x'] = spx
    sni = _q(params, 'sni', 'peer', 'servername')
    if sni:
        node['servername'] = sni
    fp = _q(params, 'fp')
    if fp:
        node['client-fingerprint'] = fp
    if _q(params, 'allowInsecure') in ('1', 'true'):
        node['skip-cert-verify'] = True
    alpn = _q(params, 'alpn')
    if alpn:
        node['alpn'] = [x for x in alpn.split(',') if x]
    net = _q(params, 'type', default='tcp').lower()
    net = {'tcp': 'tcp', 'ws': 'ws', 'grpc': 'grpc', 'h2': 'h2', 'http': 'http',
           'quic': 'quic', 'xhttp': 'ws', 'httpupgrade': 'ws',
           'splithttp': 'ws'}.get(net, net)
    node['network'] = net
    host = _q(params, 'host')
    path = _q(params, 'path')
    if net == 'ws':
        node['ws-opts'] = {'path': path or '/',
                           'headers': {'Host': host} if host else {}}
    elif net == 'grpc':
        node['grpc-opts'] = {'grpc-service-name': _q(params, 'serviceName', 'servicename', 'path')}
    elif net == 'h2':
        node['h2-opts'] = {'path': path or '/', 'host': [host] if host else []}
    elif net == 'http':
        node['http-opts'] = {'path': [path or '/'],
                             'headers': {'Host': [host]} if host else {}}
    if not node['port']:
        return None
    return node


def parse_trojan(uri, name=''):
    u = urlparse(uri)
    if not u.hostname:
        return None
    params = parse_qs(u.query)
    node = {'type': 'trojan', 'name': _strip_name(u.fragment) or name,
            'server': u.hostname, 'port': u.port or 0,
            'password': unquote(u.username or '')}
    if u.password:
        node['password'] = unquote(u.username or '') + ':' + unquote(u.password)
    sni = _q(params, 'sni', 'peer')
    if sni:
        node['sni'] = sni
    if _q(params, 'allowInsecure') in ('1', 'true'):
        node['skip-cert-verify'] = True
    fp = _q(params, 'fp')
    if fp:
        node['client-fingerprint'] = fp
    alpn = _q(params, 'alpn')
    if alpn:
        node['alpn'] = [x for x in alpn.split(',') if x]
    net = _q(params, 'type', default='tcp').lower()
    net = {'tcp': 'tcp', 'ws': 'ws', 'grpc': 'grpc', 'h2': 'h2'}.get(net, net)
    node['network'] = net
    host = _q(params, 'host')
    path = _q(params, 'path')
    if net == 'ws':
        node['ws-opts'] = {'path': path or '/',
                           'headers': {'Host': host} if host else {}}
    elif net == 'grpc':
        node['grpc-opts'] = {'grpc-service-name': _q(params, 'serviceName', 'path')}
    return node


def parse_hysteria2(uri, name=''):
    u = urlparse(uri)
    if not u.hostname:
        return None
    params = parse_qs(u.query)
    auth = _q(params, 'auth')
    if not auth:
        if u.password:
            auth = unquote(u.username or '') + ':' + unquote(u.password)
        else:
            auth = unquote(u.username or '')
    node = {'type': 'hysteria2', 'name': _strip_name(u.fragment) or name,
            'server': u.hostname, 'port': u.port or 443, 'password': auth}
    obfs = _q(params, 'obfs')
    if obfs:
        node['obfs'] = obfs
        node['obfs-password'] = _q(params, 'obfs-password', 'obfs_password')
    sni = _q(params, 'sni', 'peer')
    if sni:
        node['sni'] = sni
    if _q(params, 'insecure', 'allowInsecure') in ('1', 'true'):
        node['skip-cert-verify'] = True
    alpn = _q(params, 'alpn')
    if alpn:
        node['alpn'] = [x for x in alpn.split(',') if x]
    return node


def parse_hysteria(uri, name=''):
    u = urlparse(uri)
    if not u.hostname:
        return None
    params = parse_qs(u.query)
    node = {'type': 'hysteria', 'name': _strip_name(u.fragment) or name,
            'server': u.hostname, 'port': u.port or 443}
    auth = _q(params, 'auth', 'auth-str')
    if auth:
        node['auth-str'] = auth
    for k_uri, k in (('protocol', 'protocol'), ('upmbps', 'up'),
                     ('downmbps', 'down'), ('obfs', 'obfs')):
        v = _q(params, k_uri)
        if v:
            node[k] = v if k in ('protocol', 'obfs') else _i(v)
    peer = _q(params, 'peer', 'sni')
    if peer:
        node['sni'] = peer
    if _q(params, 'insecure') in ('1', 'true'):
        node['skip-cert-verify'] = True
    alpn = _q(params, 'alpn')
    if alpn:
        node['alpn'] = [x for x in alpn.split(',') if x]
    return node


def parse_tuic(uri, name=''):
    u = urlparse(uri)
    if not u.hostname:
        return None
    params = parse_qs(u.query)
    node = {'type': 'tuic', 'name': _strip_name(u.fragment) or name,
            'server': u.hostname, 'port': u.port or 443,
            'uuid': unquote(u.username or ''), 'password': unquote(u.password or '')}
    cc = _q(params, 'congestion_control', 'congestion-controller')
    if cc:
        node['congestion-controller'] = cc
    urm = _q(params, 'udp_relay_mode', 'udp-relay-mode')
    if urm:
        node['udp-relay-mode'] = urm
    sni = _q(params, 'sni', 'peer')
    if sni:
        node['sni'] = sni
    if _q(params, 'allow_insecure', 'insecure') in ('1', 'true'):
        node['skip-cert-verify'] = True
    alpn = _q(params, 'alpn')
    if alpn:
        node['alpn'] = [x for x in alpn.split(',') if x]
    return node


def parse_anytls(uri, name=''):
    u = urlparse(uri)
    if not u.hostname:
        return None
    params = parse_qs(u.query)
    node = {'type': 'anytls', 'name': _strip_name(u.fragment) or name,
            'server': u.hostname, 'port': u.port or 443,
            'password': unquote(u.username or '')}
    if u.password:
        node['password'] = unquote(u.username or '') + ':' + unquote(u.password)
    sni = _q(params, 'sni', 'peer')
    if sni:
        node['sni'] = sni
    if _q(params, 'insecure', 'allowInsecure') in ('1', 'true'):
        node['skip-cert-verify'] = True
    fp = _q(params, 'fp')
    if fp:
        node['client-fingerprint'] = fp
    return node


def parse_wireguard(uri, name=''):
    body = uri.split('://', 1)[1]
    frag = ''
    if '#' in body:
        body, frag = body.split('#', 1)
    query = ''
    if '?' in body:
        body, query = body.split('?', 1)
    params = parse_qs(query)
    hostport = body
    pk = ''
    if '@' in body:
        pk, _, hostport = body.rpartition('@')
    server, port = _split_hostport(hostport)
    node = {'type': 'wireguard', 'name': _strip_name(frag) or name,
            'server': server, 'port': port or 51820,
            'private-key': unquote(pk)}
    pub = _q(params, 'publickey', 'public-key', 'peer')
    if pub:
        node['public-key'] = pub
    addr = _q(params, 'address', 'ip')
    if addr:
        node['ip'] = addr.split(',')[0].strip()
    v6 = _q(params, 'ipv6', 'addressv6')
    if v6:
        node['ipv6'] = v6
    if _q(params, 'reserved'):
        try:
            node['reserved'] = [int(x) for x in _q(params, 'reserved').split(',')]
        except Exception:
            pass
    mtu = _q(params, 'mtu')
    if mtu:
        node['mtu'] = _i(mtu)
    if _q(params, 'udp'):
        node['udp'] = _b(_q(params, 'udp'))
    if not server:
        return None
    return node


def parse_http_socks(uri, name=''):
    scheme = uri.split('://', 1)[0].lower()
    u = urlparse(uri)
    if not u.hostname:
        return None
    t = 'socks5' if 'socks' in scheme else 'http'
    node = {'type': t, 'name': _strip_name(u.fragment) or name,
            'server': u.hostname, 'port': u.port or (1080 if t == 'socks5' else 80)}
    user = unquote(u.username or '')
    pw = unquote(u.password or '')
    # 部分订阅把 user:pass 整体做了 base64
    if user and not pw:
        dec = try_b64_text(user)
        if dec and ':' in dec:
            user, _, pw = dec.partition(':')
    if user:
        node['username'] = user
    if pw:
        node['password'] = pw
    if scheme == 'https':
        node['tls'] = True
    return node


def parse_snell(uri, name=''):
    u = urlparse(uri)
    if not u.hostname:
        return None
    params = parse_qs(u.query)
    node = {'type': 'snell', 'name': _strip_name(u.fragment) or name,
            'server': u.hostname, 'port': u.port or 443,
            'psk': unquote(u.username or '')}
    v = _q(params, 'version')
    if v:
        node['version'] = _i(v, 3)
    obfs = _q(params, 'obfs')
    if obfs:
        node['obfs-opts'] = {'mode': obfs, 'host': _q(params, 'obfs-host', 'host')}
    return node


def parse_ssh(uri, name=''):
    u = urlparse(uri)
    if not u.hostname:
        return None
    node = {'type': 'ssh', 'name': _strip_name(u.fragment) or name,
            'server': u.hostname, 'port': u.port or 22,
            'username': unquote(u.username or 'root')}
    if u.password:
        node['password'] = unquote(u.password)
    return node


# ---------------------------------------------------------------- 分发
URI_PARSERS = [
    ('ssr://', lambda u, n='': parse_ssr(u)),
    ('ss://', parse_ss),
    ('vmess://', parse_vmess),
    ('vless://', parse_vless),
    ('trojan-go://', parse_trojan),
    ('trojan://', parse_trojan),
    ('hysteria2://', parse_hysteria2),
    ('hy2://', parse_hysteria2),
    ('hysteria://', parse_hysteria),
    ('tuic://', parse_tuic),
    ('anytls://', parse_anytls),
    ('wireguard://', parse_wireguard),
    ('wg://', parse_wireguard),
    ('socks5://', parse_http_socks),
    ('socks://', parse_http_socks),
    ('socks4://', parse_http_socks),
    ('https://', parse_http_socks),
    ('http://', parse_http_socks),
    ('snell://', parse_snell),
    ('ssh://', parse_ssh),
]


def parse_node_uri(uri):
    uri = uri.strip()
    if not uri or uri.startswith(('#', '//', ';')):
        return None
    for prefix, fn in URI_PARSERS:
        if uri.lower().startswith(prefix):
            try:
                node = fn(uri)
            except Exception:
                return None
            if node and node.get('server') and node.get('port'):
                return node
            return None
    return None


# ---------------------------------------------------------------- 订阅体识别
SCHEMES = ('ss://', 'ssr://', 'vmess://', 'vless://', 'trojan://', 'trojan-go://',
           'hysteria://', 'hysteria2://', 'hy2://', 'tuic://', 'anytls://',
           'wireguard://', 'wg://', 'socks5://', 'socks://', 'socks4://',
           'snell://', 'ssh://')


def looks_like_links(text):
    t = text.lstrip()
    return any(t.lower().startswith(s) or ('\n' + s) in t.lower()
               for s in SCHEMES)


def parse_links_text(text):
    """从任意文本提取 URI 链接, 顺带尝试整段 base64"""
    nodes = []
    text = text.strip()
    if not text:
        return nodes
    # 先尝试整段 base64(常见订阅形态)
    if not looks_like_links(text) and re.fullmatch(r'[A-Za-z0-9+/=_\-\s]+', text) and len(text) > 40:
        dec = b64d_str(text)
        if dec and looks_like_links(dec):
            text = dec
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if looks_like_links(line):
            # 抓一行里可能的多个链接(少见但存在)
            for m in re.finditer(r'\S+://\S+', line):
                node = parse_node_uri(m.group(0))
                if node:
                    nodes.append(node)
        else:
            # 单行也可能是 base64 单条链接
            m = re.search(r'(ss|ssr|vmess|vless|trojan|hysteria2?|hy2|tuic|anytls|wireguard|snell)://\S+',
                          line, re.I)
            if m:
                node = parse_node_uri(m.group(0))
                if node:
                    nodes.append(node)
    return nodes


CLASH_TYPES = {'ss', 'ssr', 'vmess', 'vless', 'trojan', 'hysteria', 'hysteria2',
               'tuic', 'anytls', 'wireguard', 'http', 'socks5', 'snell', 'ssh', 'mieru',
               'juicity', 'shadowtls', 'ssh'}


def parse_clash_yaml(text):
    """解析 mihomo / clash 配置, 抽取 proxies"""
    import yaml
    try:
        loader = getattr(yaml, 'CSafeLoader', yaml.SafeLoader)
        doc = yaml.load(text, Loader=loader)
    except Exception:
        return []
    if not isinstance(doc, dict):
        return []
    px = doc.get('proxies') or []
    nodes = []
    for p in px:
        if not isinstance(p, dict):
            continue
        t = str(p.get('type', '')).lower()
        if t not in CLASH_TYPES:
            continue
        if not p.get('server') or not p.get('port'):
            continue
        node = dict(p)
        node['type'] = t
        node['port'] = _i(node['port'])
        nodes.append(node)
    return nodes


def parse_singbox_json(text):
    """解析 sing-box 出站 JSON, 转 mihomo 风格"""
    try:
        j = json.loads(text)
    except Exception:
        return []
    outs = j.get('outbounds') if isinstance(j, dict) else (j if isinstance(j, list) else [])
    if not isinstance(outs, list):
        return []
    nodes = []
    for o in outs:
        if not isinstance(o, dict):
            continue
        t = str(o.get('type', '')).lower()
        server = o.get('server', '')
        port = _i(o.get('server_port', 0))
        if not server or not port:
            continue
        name = o.get('tag', f'{server}:{port}')
        tls = o.get('tls') or {}
        if not isinstance(tls, dict):
            tls = {}
        node = None
        if t == 'shadowsocks':
            node = {'type': 'ss', 'cipher': o.get('method', ''),
                    'password': o.get('password', '')}
        elif t == 'vmess':
            node = {'type': 'vmess', 'uuid': o.get('uuid', ''),
                    'alterId': _i(o.get('alter_id', 0)),
                    'cipher': o.get('security', 'auto') or 'auto', 'network': 'tcp'}
            tr = o.get('transport') or {}
            _sb_transport(node, tr, tls)
        elif t == 'vless':
            node = {'type': 'vless', 'uuid': o.get('uuid', ''),
                    'flow': o.get('flow', ''), 'network': 'tcp'}
            tr = o.get('transport') or {}
            _sb_transport(node, tr, tls)
        elif t == 'trojan':
            node = {'type': 'trojan', 'password': o.get('password', ''),
                    'network': 'tcp'}
            tr = o.get('transport') or {}
            _sb_transport(node, tr, tls)
        elif t == 'hysteria2':
            node = {'type': 'hysteria2', 'password': o.get('password', ''),
                    'obfs': (o.get('obfs') or {}).get('type', ''),
                    'obfs-password': (o.get('obfs') or {}).get('password', '')}
        elif t == 'tuic':
            node = {'type': 'tuic', 'uuid': o.get('uuid', ''),
                    'password': o.get('password', ''),
                    'congestion-controller': o.get('congestion_control', ''),
                    'udp-relay-mode': o.get('udp_relay_mode', '')}
        elif t == 'anytls':
            node = {'type': 'anytls', 'password': o.get('password', ''),
                    'network': 'tcp'}
            tr = o.get('transport') or {}
            _sb_transport(node, tr, tls)
        if not node:
            continue
        node.update({'name': name, 'server': server, 'port': port})
        if tls:
            node['tls'] = True
            if tls.get('server_name'):
                node['servername'] = tls['server_name']
            if tls.get('insecure'):
                node['skip-cert-verify'] = True
            if tls.get('alpn'):
                node['alpn'] = tls['alpn']
            if tls.get('utls', {}).get('fingerprint'):
                node['client-fingerprint'] = tls['utls']['fingerprint']
            re_ = tls.get('reality') or {}
            if isinstance(re_, dict) and re_.get('enabled'):
                node['reality-opts'] = {'public-key': re_.get('public_key', ''),
                                        'short-id': re_.get('short_id', '')}
        nodes.append(node)
    return nodes


def _sb_transport(node, tr, tls):
    tt = str(tr.get('type', '')).lower() if isinstance(tr, dict) else ''
    if tt == 'ws':
        node['network'] = 'ws'
        node['ws-opts'] = {'path': tr.get('path', '/'),
                           'headers': {'Host': (tr.get('headers') or {}).get('Host', '')}}
    elif tt in ('grpc',):
        node['network'] = 'grpc'
        node['grpc-opts'] = {'grpc-service-name': tr.get('service_name', '')}
    elif tt in ('http', 'h2'):
        node['network'] = 'h2'
        node['h2-opts'] = {'path': tr.get('path', '/'),
                           'host': tr.get('host', [])}
    elif tt == 'quic':
        node['network'] = 'quic'


def parse_subscription(text):
    """总入口: 自动识别格式 -> 节点列表; 返回 (nodes, format_name)"""
    if not text or not text.strip():
        return [], 'empty'
    head = text.lstrip()[:4096]
    low = head.lower()
    if low.startswith('{') or '"outbounds"' in head[:2000]:
        nodes = parse_singbox_json(text)
        if nodes:
            return nodes, 'singbox-json'
    if 'proxies:' in low or (low.startswith(('port:', 'mixed-port:', 'proxy-providers:'))
                             and 'proxies' in low):
        nodes = parse_clash_yaml(text)
        if nodes:
            return nodes, 'clash-yaml'
    # base64 / 明文
    nodes = parse_links_text(text)
    fmt = 'links' if looks_like_links(text.lstrip()) else 'base64-links'
    return nodes, fmt


def dedup(nodes):
    """按 type+server+port+凭证 去重, 保序"""
    seen, out = set(), []
    for n in nodes:
        key = (n.get('type'), n.get('server'), n.get('port'),
               n.get('uuid') or n.get('password') or n.get('psk') or '',
               json.dumps(n.get('ws-opts', n.get('grpc-opts', {})), sort_keys=True, ensure_ascii=False))
        if key in seen:
            continue
        seen.add(key)
        out.append(n)
    return out


# 各协议必填字段 (缺失的节点直接丢弃, 否则会毁掉整份配置)
_REQUIRED = {
    'ss': ('server', 'port', 'cipher', 'password'),
    'ssr': ('server', 'port', 'cipher', 'password'),
    'vmess': ('server', 'port', 'uuid'),
    'vless': ('server', 'port', 'uuid'),
    'trojan': ('server', 'port', 'password'),
    'hysteria2': ('server', 'port', 'password'),
    'hysteria': ('server', 'port'),
    'tuic': ('server', 'port'),
    'anytls': ('server', 'port', 'password'),
    'snell': ('server', 'port', 'psk'),
    'wireguard': ('server', 'port', 'private-key'),
    'ssh': ('server', 'port'),
    'http': ('server', 'port'),
    'socks5': ('server', 'port'),
}


def validate_nodes(nodes):
    """过滤缺必填字段的脏节点, 返回 (干净列表, {类型: 丢弃数})"""
    out, dropped = [], {}
    for n in nodes:
        req = _REQUIRED.get(n.get('type'))
        bad = req is None
        if not bad:
            for k in req:
                if n.get(k) in (None, '', 0):
                    bad = True
                    break
        if n.get('type') == 'tuic' and not (n.get('uuid') or n.get('token') or n.get('password')):
            bad = True
        if bad:
            t = n.get('type', '?')
            dropped[t] = dropped.get(t, 0) + 1
            continue
        out.append(n)
    return out, dropped


def _region_of(name):
    """从节点名猜地区"""
    s = (name or '') + ''
    table = [
        (('香港', 'HK', 'Hong', '🇭🇰'), 'HK'), (('台湾', '台灣', 'TW', 'Taiwan', '🇹🇼'), 'TW'),
        (('日本', 'JP', 'Japan', '东京', '大阪', '🇯🇵'), 'JP'), (('新加坡', 'SG', 'Singapore', '狮城', '🇸🇬'), 'SG'),
        (('美国', 'US', 'United', '洛杉矶', '圣何塞', '西雅图', '🇺🇸'), 'US'),
        (('韩国', 'KR', 'Korea', '首尔', '🇰🇷'), 'KR'), (('英国', 'UK', 'GB', 'London', '🇬🇧'), 'UK'),
        (('德国', 'DE', 'German', '🇩🇪'), 'DE'), (('法国', 'FR', 'France', '🇫🇷'), 'FR'),
        (('加拿大', 'CA', 'Canada', '🇨🇦'), 'CA'), (('澳大利亚', 'AU', 'Australia', '🇦🇺'), 'AU'),
        (('俄罗斯', 'RU', 'Russia', '🇷🇺'), 'RU'), (('印度', 'IN', 'India', '🇮🇳'), 'IN'),
        (('土耳其', 'TR', 'Turkey', '🇹🇷'), 'TR'), (('巴西', 'BR', 'Brazil', '🇧🇷'), 'BR'),
        (('荷兰', 'NL', 'Netherland', '🇳🇱'), 'NL'), (('意大利', 'IT', 'Italy', '🇮🇹'), 'IT'),
        (('马来西亚', 'MY', 'Malaysia', '🇲🇾'), 'MY'), (('泰国', 'TH', 'Thai', '🇹🇭'), 'TH'),
        (('越南', 'VN', 'Vietnam', '🇻🇳'), 'VN'), (('菲律宾', 'PH', 'Philippine', '🇵🇭'), 'PH'),
        (('印尼', 'ID', 'Indonesia', '🇮🇩'), 'ID'), (('阿根廷', 'AR', 'Argentina', '🇦🇷'), 'AR'),
        (('中国', 'CN', 'China', '回国', '🇨🇳'), 'CN'),
    ]
    for keys, cc in table:
        for k in keys:
            if k in s:
                return cc
    if '🇭🇰' in s:
        return 'HK'
    return 'XX'


def stats(nodes):
    from collections import Counter
    types = Counter(n.get('type', '?') for n in nodes)
    regions = Counter(_region_of(n.get('name', '')) for n in nodes)
    return {'total': len(nodes), 'types': dict(types),
            'regions': dict(regions)}
