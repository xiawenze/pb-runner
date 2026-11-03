#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""alive — 节点 TCP 快测 (asyncio 并发), UDP 系协议标 unknown 不误杀"""
import asyncio

UDP_TYPES = {'hysteria', 'hysteria2', 'tuic', 'wireguard', 'ssh'}


def _endpoint(n):
    s = str(n.get('server') or '').strip()
    p = n.get('port') or 0
    try:
        p = int(p)
    except (TypeError, ValueError):
        p = 0
    if not s or not p:
        return None
    return s, p


async def _ping(ep, sem, timeout):
    host, port = ep
    async with sem:
        try:
            fut = asyncio.open_connection(host, port)
            r, w = await asyncio.wait_for(fut, timeout)
            try:
                w.close()
            except Exception:
                pass
            return True
        except Exception:
            return False


def fastping(nodes, timeout=2.5, concurrency=128, progress=None):
    """nodes: parse.py 出的节点列表
    返回 {'alive':[节点名], 'dead':[节点名], 'unknown':[节点名], 'ok_eps':set((h,p))}
    progress(done, total) 周期回调"""
    eps, udp_eps = {}, {}
    for n in nodes:
        ep = _endpoint(n)
        if not ep:
            continue
        t = str(n.get('type') or n.get('protocol') or '').lower()
        name = str(n.get('name') or f"{ep[0]}:{ep[1]}")
        if t in UDP_TYPES:
            udp_eps.setdefault(ep, []).append(name)
        else:
            eps.setdefault(ep, []).append(name)

    all_eps = list(eps.keys())
    total = len(all_eps)

    async def run():
        sem = asyncio.Semaphore(concurrency)
        fut_map = {ep: asyncio.ensure_future(_ping(ep, sem, timeout)) for ep in all_eps}
        ep_by_fut = {f: e for e, f in fut_map.items()}
        results, pending = {}, set(fut_map.values())
        while pending:
            done_set, pending = await asyncio.wait(pending, timeout=5,
                                                   return_when=asyncio.FIRST_COMPLETED)
            for t in done_set:
                results[ep_by_fut.get(t)] = t.result()
            if progress:
                progress(len(results), total)
        return results

    try:
        results = asyncio.run(run())
    except RuntimeError:
        results = {ep: False for ep in all_eps}

    alive, dead, ok_eps = [], [], set()
    for ep, names in eps.items():
        if results.get(ep):
            ok_eps.add(ep)
            alive.extend(names)
        else:
            dead.extend(names)
    unknown = [n for names in udp_eps.values() for n in names]
    return {'alive': alive, 'dead': dead, 'unknown': unknown, 'ok_eps': ok_eps}
