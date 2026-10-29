#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配件资讯采集器
数据源：IT之家 RSS（https://www.ithome.com/rss/）
输出：data/news.json

每条包含：标题、链接、发布时间、摘要、封面图、分类
用法：python3 news.py
"""
import os
import re
import json
import time
import html
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
OUT = os.path.join(ROOT, "data", "news.json")
CACHE = os.path.join(BASE, "cache", "news.xml")

FEEDS = [
    "https://www.ithome.com/rss/",
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 分类优先级：命中即停
RULES = [
    ("gpu", "显卡", ["RTX", "GeForce", "Radeon", "显卡", "GPU", "显存", "DLSS", "FSR", "光线追踪", "光追"]),
    ("cpu", "CPU", ["锐龙", "酷睿", "Ryzen", "处理器", "CPU", "至强", "EPYC", "Core Ultra", "指令集", "制程"]),
    ("mem", "内存", ["内存", "DDR5", "DDR4", "DRAM", "LPDDR", "内存条", "颗粒"]),
    ("ssd", "存储", ["固态", "SSD", "硬盘", "NAND", "闪存", "存储", "UFS"]),
    ("mb", "主板", ["主板", "X870", "B850", "Z890", "B760", "B650", "芯片组"]),
    ("monitor", "显示器", ["显示器", "屏幕", "OLED", "面板", "刷新率", "分辨率", "色域", "Mini LED"]),
    ("peripheral", "外设", ["键盘", "鼠标", "耳机", "手柄", "外设", "键鼠", "轴体"]),
    ("cooler", "散热", ["散热器", "水冷", "风冷", "风扇", "硅脂", "导热", "冷排"]),
    ("case", "机箱", ["机箱", "海景房", "风道", "侧透"]),
    ("psu", "电源", ["电源", "80PLUS", "金牌电源", "ATX 3"]),
    ("pc", "整机", ["笔记本", "台式机", "整机", "掌机", "迷你主机", "装机", "一体机"]),
]

# 归入「行业」前，必须命中下列硬件相关词，否则视为不相关资讯丢弃
HARDWARE_HINT = [
    "芯片", "半导体", "晶圆", "代工", "制程", "存储", "面板", "供应链", "硬件", "DIY",
    "显卡", "处理器", "内存", "硬盘", "主板", "显示器", "散热", "机箱", "电源", "外设",
    "台积电", "三星", "SK海力士", "美光", "长江存储", "摩尔线程", "龙芯", "RISC-V",
]


def classify(title, desc):
    """返回 (cat, tag)；与 PC 硬件无关的返回 None 表示丢弃"""
    text = (title + " " + desc).lower()
    for key, tag, kws in RULES:
        for kw in kws:
            if kw.lower() in text:
                return key, tag

    for kw in HARDWARE_HINT:
        if kw.lower() in text:
            return "industry", "行业"
    return None


def clean_html(s):
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r"<br\s*/?>", " ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def first_img(s):
    if not s:
        return ""
    raw = html.unescape(s)
    m = re.search(r'<img[^>]+src="([^"]+)"', raw)
    return m.group(1) if m else ""


def write_log(entry):
    """写入统一的采集日志 data/collect-log.json"""
    path = os.path.join(ROOT, "data", "collect-log.json")
    data = {"updated": "", "entries": []}
    if os.path.exists(path):
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            pass
    entries = data.get("entries", [])
    entries.insert(0, entry)
    data["entries"] = entries[:60]
    data["updated"] = entry.get("time", "")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def fetch(url, retry=3):
    for i in range(retry):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read()
        except Exception as e:
            if i == retry - 1:
                print("! 拉取失败", e)
                return b""
            time.sleep(1.5)
    return b""


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        from email.utils import parsedate_to_datetime
        d = parsedate_to_datetime(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone(timedelta(hours=8)))
        return d.isoformat(timespec="seconds")
    except Exception:
        return None


def main():
    t0 = time.time()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)

    seen, items = set(), []
    for feed in FEEDS:
        raw = fetch(feed)
        if not raw:
            continue
        with open(CACHE, "wb") as f:
            f.write(raw)

        try:
            root = ET.fromstring(raw)
        except Exception as e:
            print("XML 解析失败", e)
            continue

        for node in root.iter("item"):
            title = (node.findtext("title") or "").strip()
            link = (node.findtext("link") or "").strip()
            if not title or not link or link in seen:
                continue
            seen.add(link)

            desc_raw = node.findtext("description") or ""
            desc = clean_html(desc_raw)
            # 去掉「IT之家 10 月 27 日消息，」这类固定前缀
            desc = re.sub(r"^IT之家\s*\d+\s*月\s*\d+\s*日消息[，,：:]\s*", "", desc)
            if len(desc) > 110:
                desc = desc[:110].rstrip() + "…"

            hit = classify(title, desc)
            if not hit:
                continue
            cat, tag = hit
            items.append({
                "title": title,
                "url": link,
                "time": parse_date(node.findtext("pubDate") or ""),
                "desc": desc,
                "img": first_img(desc_raw),
                "cat": cat,
                "tag": tag,
                "source": "IT之家",
            })

    items.sort(key=lambda x: x["time"] or "", reverse=True)

    payload = {
        "updated": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "source": "IT之家 RSS",
        "count": len(items),
        "items": items,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    from collections import Counter
    c = Counter(i["tag"] for i in items)
    print(f"共 {len(items)} 条 · " + " ".join(f"{k}{v}" for k, v in c.most_common()))
    print("已写入", OUT)

    n = len(items)
    status = "ok" if n >= 10 else ("partial" if n > 0 else "fail")
    msg = "资讯抓取正常" if n >= 10 else ("资讯数量偏少，可能源站异常" if n > 0 else "未获取到任何资讯")
    write_log({
        "time": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "type": "news", "status": status, "count": n,
        "duration": round(time.time() - t0, 1), "message": msg
    })


if __name__ == "__main__":
    main()
