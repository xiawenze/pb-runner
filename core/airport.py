#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""airport - 从订阅链接/正文推断机场品牌名（识别不到返回空串，不抛错）"""
import re
import urllib.parse

SIGNS = [
    ("sakuracat", "SakuraCat"),
    ("liangxin", "LiangXinYun"),
    ("mianhuayun", "MianHuaYun"),
    ("peiqian", "PeiQian"),
    ("dasho", "Dasho"),
    ("smallstrawberry", "SmallStrawberry"),
    ("niva", "Niva"),
    ("ikuuu", "iKuuu"),
    ("tohno", "Tohno"),
    ("madvine", "Madvine"),
    ("mizuku", "Mizuku"),
    ("glados", "GLaDOS"),
    ("mjhy2", "mjhy2"),
    ("louwangzhiyu", "LouWangZhiYu"),
    ("yywhale", "yywhale"),
    ("huaxia", "HuaXia"),
    ("yuyun", "YuYun"),
    ("wuruiqi", "WuRuiQi"),
    ("icorad", "Icorad"),
    ("yuntuike", "YunTuKe"),
    ("wesleyww", "Wesley"),
    ("wesley", "Wesley"),
]

TLDS = set("""cdn sub nolink one top xyz cc uk io ai cn hk tw jp kr us app dev me fun site
online shop club link network tech space live win icu cyou monster com net org biz info
pro pw ws tv""".split())

PREFIX_RE = r"^(www|sub2|sub3|sub|node|nodes|api|v|s|dl|download2|download|cdn2|cdn3|cdn|panel|master|main|jms|jc|m|gw|gateway|traffic|stream|data|link|links|conf|config|subscribe|subs|get|getnode|a)\."


def domain(url):
    """URL -> 小写主机名，失败返回空"""
    try:
        u = urllib.parse.urlparse((url or "").split("#")[0].strip())
    except Exception:
        return ""
    return (u.hostname or "").lower()


def root_label(url):
    """去掉 CDN 子域与后缀: sub.wesleyww.cc.cd -> wesleyww"""
    host = domain(url)
    if not host:
        return ""
    parts = [p for p in host.split(".") if p]
    while len(parts) > 2 and re.match(PREFIX_RE, parts[0] + ".", re.I):
        parts = parts[1:]
    while len(parts) > 1:
        pair = parts[-2] + "." + parts[-1] if len(parts) >= 2 else ""
        if pair in DOUBLE_TLD and len(parts) > 2:
            parts = parts[:-2]
            continue
        if parts[-1] in TLDS and len(parts) > 1:
            parts = parts[:-1]
            continue
        break
    label = parts[-1] if parts else ""
    if not label or label.isdigit() or len(label) < 3:
        return ""
    return label


def from_domain(url):
    """sub.wesleyww.cc.cd -> Wesleyww"""
    label = root_label(url)
    if not label:
        return ""
    return label[:1].upper() + label[1:]


def match_brand(url="", text="", title="", web_page=""):
    """在 url/标题/网页地址/正文前 4000 字里找已知品牌特征词"""
    hay = " ".join([url or "", title or "", web_page or "", (text or "")[:4000]]).lower()
    for key, name in SIGNS:
        if key in hay:
            return name
    return ""


def guess(url="", text="", title="", web_page=""):
    """优先响应头 profile-title，其次品牌特征词，最后域名主标签"""
    t = (title or "").strip()
    if t:
        t = re.sub(r"^[\s\W_]+|[\s\W_]+$", "", t)
        t = re.sub(r"(\u8ba2\u9605|subscription|config|clash)$", "", t, flags=re.I).strip()
        if 2 <= len(t) <= 24:
            return t
    b = match_brand(url, text, "", web_page)
    if b:
        return b
    return from_domain(url)


FLAGS = [
    ("HK", "\U0001F1ED\U0001F1F0"), ("TW", "\U0001F1F9\U0001F1FC"),
    ("JP", "\U0001F1EF\U0001F1F5"), ("SG", "\U0001F1F8\U0001F1EC"),
    ("US", "\U0001F1FA\U0001F1F8"), ("KR", "\U0001F1F0\U0001F1F7"),
    ("UK", "\U0001F1EC\U0001F1E7"), ("GB", "\U0001F1EC\U0001F1E7"),
    ("DE", "\U0001F1E9\U0001F1EA"), ("FR", "\U0001F1EB\U0001F1F7"),
    ("NL", "\U0001F1F3\U0001F1F1"), ("CA", "\U0001F1E8\U0001F1E6"),
    ("AU", "\U0001F1E6\U0001F1FA"), ("IN", "\U0001F1EE\U0001F1F3"),
    ("RU", "\U0001F1F7\U0001F1FA"), ("TR", "\U0001F1F9\U0001F1F7"),
    ("VN", "\U0001F1FB\U0001F1F3"), ("TH", "\U0001F1F9\U0001F1ED"),
    ("MY", "\U0001F1F2\U0001F1FE"), ("ID", "\U0001F1EE\U0001F1E9"),
    ("PH", "\U0001F1F5\U0001F1ED"), ("AE", "\U0001F1E6\U0001F1EA"),
    ("BR", "\U0001F1E7\U0001F1F7"), ("AR", "\U0001F1E6\U0001F1F7"),
    ("MX", "\U0001F1F2\U0001F1FD"), ("ZA", "\U0001F1FF\U0001F1E6"),
    ("CH", "\U0001F1E8\U0001F1ED"), ("IT", "\U0001F1EE\U0001F1F9"),
    ("ES", "\U0001F1EA\U0001F1F8"), ("PL", "\U0001F1F5\U0001F1F1"),
    ("FI", "\U0001F1EB\U0001F1EE"), ("SE", "\U0001F1F8\U0001F1EA"),
    ("NO", "\U0001F1F3\U0001F1F4"), ("IE", "\U0001F1EE\U0001F1EA"),
]

CN_FLAGS = [
    ("\u9999\u6e2f", "\U0001F1ED\U0001F1F0"), ("\u53f0\u6e7e", "\U0001F1F9\U0001F1FC"),
    ("\u4e2d\u56fd\u53f0\u5317", "\U0001F1F9\U0001F1FC"), ("\u65e5\u672c", "\U0001F1EF\U0001F1F5"),
    ("\u65b0\u52a0\u5761", "\U0001F1F8\U0001F1EC"), ("\u7f8e\u56fd", "\U0001F1FA\U0001F1F8"),
    ("\u97e9\u56fd", "\U0001F1F0\U0001F1F7"), ("\u82f1\u56fd", "\U0001F1EC\U0001F1E7"),
    ("\u5fb7\u56fd", "\U0001F1E9\U0001F1EA"), ("\u6cd5\u56fd", "\U0001F1EB\U0001F1F7"),
    ("\u8377\u5170", "\U0001F1F3\U0001F1F1"), ("\u52a0\u62ff\u5927", "\U0001F1E8\U0001F1E6"),
    ("\u6fb3\u5927\u5229\u4e9a", "\U0001F1E6\U0001F1FA"), ("\u5370\u5ea6", "\U0001F1EE\U0001F1F3"),
    ("\u4fc4\u7f57\u65af", "\U0001F1F7\U0001F1FA"), ("\u571f\u8033\u5176", "\U0001F1F9\U0001F1F7"),
    ("\u8d8a\u5357", "\U0001F1FB\U0001F1F3"), ("\u6cf0\u56fd", "\U0001F1F9\U0001F1ED"),
    ("\u9a6c\u6765\u897f\u4e9a", "\U0001F1F2\U0001F1FE"), ("\u5370\u5c3c", "\U0001F1EE\U0001F1E9"),
    ("\u83f2\u5f8b\u5bbe", "\U0001F1F5\U0001F1ED"), ("\u8fea\u62dc", "\U0001F1E6\U0001F1EA"),
    ("\u963f\u8054\u914b", "\U0001F1E6\U0001F1EA"), ("\u5df4\u897f", "\U0001F1E7\U0001F1F7"),
    ("\u610f\u5927\u5229", "\U0001F1EE\U0001F1F9"), ("\u745e\u58eb", "\U0001F1E8\U0001F1ED"),
    ("\u897f\u73ed\u7259", "\U0001F1EA\U0001F1F8"), ("\u82ac\u5170", "\U0001F1EB\U0001F1EE"),
    ("\u745e\u5178", "\U0001F1F8\U0001F1EA"), ("\u632a\u5a01", "\U0001F1F3\U0001F1F4"),
]


def flag_of(url="", text=""):
    """从域名里的国家代码或正文里的中文国名掐一面国旗；识别不到返回空串"""
    code = root_label(url).upper()
    for k, v in FLAGS:
        if code == k or code.startswith(k + "2"):
            return v
    head = (text or "")[:3000]
    for k, v in CN_FLAGS:
        if k in head:
            return v
    return ""


def display(url="", text="", title="", web_page=""):
    """最终展示名：国旗 + 品牌；品牌缺失就只回国旗"""
    name = guess(url, text, title, web_page)
    f = flag_of(url, text)
    return (f + " " + name).strip() if name else f


DOUBLE_TLD = {"cc.cd", "com.cd", "co.uk", "com.cn", "net.cn", "org.cn", "com.hk",
              "com.tw", "co.jp", "ne.jp", "or.jp", "com.au", "net.au", "com.sg"}


def _is_suffix(seg):
    return seg in TLDS or any(seg == d or seg == d.split(".")[-1] for d in DOUBLE_TLD)


def flag_from_url(url="", text=""):
    """只从子域猜国旗（hk./jp./sg.）；主标签不当国家码，避免误判"""
    host = domain(url)
    if host:
        labels = [p for p in host.lower().split(".") if p]
        while len(labels) > 2 and _is_suffix(labels[-1]):
            labels.pop()
        for p in labels[:-1]:
            if len(p) == 2:
                for k, v in FLAGS:
                    if k == p.upper():
                        return v
    head = (text or "")[:1500]
    for k, v in CN_FLAGS:
        if k in head:
            return v
    return ""
