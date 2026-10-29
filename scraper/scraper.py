#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PC 配件报价采集器
数据源：中关村在线（detail.zol.com.cn）公开报价列表页

用法：
    python3 scraper.py           # 抓取 -> ../data/data.json
    python3 scraper.py --dry     # 只打印，不写文件
    python3 scraper.py --force   # 忽略当日缓存，强制重抓

数据说明：
    ZOL 公开页面只提供「当前报价」，不提供历史价格序列。
    · 当天价格：真实抓取
    · 历史曲线：首次采集时按品类波动回溯生成
    · 每天两场采集（12 点 / 23 点），快照按场次存为 YYYY-MM-DD-12.json / -23.json
    · 生成日线时，同一天取较晚一场的报价
    · 数量异常（< MIN_PRODUCTS）时中止，不覆盖线上数据
"""
import re
import os
import sys
import json
import time
import gzip
import random
import shutil
import subprocess
import urllib.request
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
DATA_DIR = os.path.join(ROOT, "data")
SNAP_DIR = os.path.join(DATA_DIR, "snapshots")
CACHE_DIR = os.path.join(BASE, "cache")
DAYS = 365
MIN_PRODUCTS = 150   # 抓取数量低于此值视为被风控，中止发布

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

CATS = [
    ("cpu",        "CPU",    "https://detail.zol.com.cn/cpu/"),
    ("gpu",        "显卡",   "https://detail.zol.com.cn/vga/"),
    ("mem",        "内存",   "https://detail.zol.com.cn/memory/"),
    ("ssd",        "存储",   "https://detail.zol.com.cn/solid_state_drive/"),
    ("mb",         "主板",   "https://detail.zol.com.cn/motherboard/"),
    ("psu",        "电源",   "https://detail.zol.com.cn/power/"),
    ("cooler",     "散热",   "https://detail.zol.com.cn/cooling_product/"),
    ("case",       "机箱",   "https://detail.zol.com.cn/case/"),
    ("monitor",    "显示器", "https://detail.zol.com.cn/lcd/"),
    ("peripheral", "外设",   "https://detail.zol.com.cn/mice/"),
]

# 品类波动率 / 年化漂移（仅用于首次回溯，不参与真实报价）
CAT_PARAMS = {
    "cpu":        (0.17, -0.06),
    "gpu":        (0.22, -0.02),
    "mem":        (0.28, +0.16),
    "ssd":        (0.15, -0.09),
    "mb":         (0.13, -0.04),
    "psu":        (0.09, -0.02),
    "cooler":     (0.11, -0.03),
    "case":       (0.08,  0.00),
    "monitor":    (0.12, -0.07),
    "peripheral": (0.10, -0.05),
}

BRANDS = [
    ("Intel", ["intel", "酷睿"]), ("AMD", ["amd", "锐龙", "ryzen", "速龙"]),
    ("NVIDIA", ["nvidia", "geforce", "rtx", "gtx"]),
    ("华硕", ["华硕", "asus", "rog", "tuf"]), ("微星", ["微星", "msi"]),
    ("技嘉", ["技嘉", "gigabyte", "aorus"]), ("七彩虹", ["七彩虹", "colorful"]),
    ("影驰", ["影驰", "galaxy"]), ("索泰", ["索泰", "zotac"]),
    ("蓝宝石", ["蓝宝石", "sapphire"]), ("铭瑄", ["铭瑄", "maxsun"]),
    ("瀚铠", ["瀚铠"]), ("金士顿", ["金士顿", "kingston", "fury"]),
    ("芝奇", ["芝奇", "g.skill", "gskill"]), ("海盗船", ["海盗船", "corsair"]),
    ("光威", ["光威", "gloway"]), ("威刚", ["威刚", "adata", "xpg"]),
    ("三星", ["三星", "samsung"]), ("西部数据", ["西部数据", "western", "wd"]),
    ("致态", ["致态", "zhitai", "tiplus"]), ("铠侠", ["铠侠", "kioxia"]),
    ("希捷", ["希捷", "seagate"]), ("振华", ["振华", "super flower"]),
    ("海韵", ["海韵", "seasonic"]), ("长城", ["长城", "great wall"]),
    ("利民", ["利民", "thermalright"]), ("猫头鹰", ["猫头鹰", "noctua"]),
    ("九州风神", ["九州风神", "deepcool"]), ("瓦尔基里", ["瓦尔基里"]),
    ("追风者", ["追风者", "phanteks"]), ("联力", ["联力", "lian li"]),
    ("先马", ["先马"]), ("小米", ["小米", "xiaomi", "redmi"]),
    ("戴尔", ["戴尔", "dell"]), ("LG", ["lg"]), ("罗技", ["罗技", "logitech"]),
    ("雷蛇", ["雷蛇", "razer"]), ("樱桃", ["樱桃", "cherry"]),
    ("达尔优", ["达尔优", "dareu"]), ("雷柏", ["雷柏", "rapoo"]),
    ("酷冷至尊", ["酷冷", "cooler master"]), ("乔思伯", ["乔思伯", "jonsbo"]),
    ("安钛克", ["安钛克", "antec"]), ("鑫谷", ["鑫谷", "segotep"]),
]

BLOCK = ["扣具", "硅脂", "支架", "线材", "转接", "硬盘盒", "保护套", "贴膜", "清洁", "风扇"]


def _decode(raw, headers):
    if (headers.get("Content-Encoding") or "").lower() == "gzip":
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    return raw.decode("gbk", errors="ignore")


def fetch_http(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip",
        "Referer": "https://detail.zol.com.cn/",
    })
    with urllib.request.urlopen(req, timeout=25) as r:
        return _decode(r.read(), r.headers)


_BROWSER = None


def browser_bin():
    """探测可用的无头浏览器（本地 chromium / CI 里的 google-chrome）"""
    global _BROWSER
    if _BROWSER is not None:
        return _BROWSER or None
    env = os.environ.get("BROWSER_BIN")
    cands = [env] if env else []
    cands += ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome"]
    for name in cands:
        if not name:
            continue
        p = name if os.path.isabs(name) and os.path.exists(name) else shutil.which(name)
        if p:
            _BROWSER = p
            return p
    _BROWSER = ""
    return None


def fetch_browser(url):
    """ZOL 频率校验页需要执行 JS，用无头浏览器兜底"""
    exe = browser_bin()
    if not exe:
        print("  ! 未找到浏览器，跳过渲染兜底")
        return ""
    cmd = [exe, "--headless=new", "--no-sandbox", "--disable-gpu",
           "--disable-dev-shm-usage", "--virtual-time-budget=11000", "--dump-dom", url]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=100)
        return r.stdout.decode("utf-8", errors="ignore")
    except Exception as e:
        print("  ! 浏览器抓取失败", e)
        return ""


def looks_blocked(html):
    return (not html) or len(html) < 4000 or "service.zol.com.cn/checking" in html


def get_page(cat, url, force=False):
    os.makedirs(CACHE_DIR, exist_ok=True)
    now = datetime.now(timezone(timedelta(hours=8)))
    day = now.strftime("%Y-%m-%d")
    slot = current_slot()
    # 缓存按「日期 + 场次」隔离，否则同一天第二场会全部命中早上的缓存
    cache = os.path.join(CACHE_DIR, f"{cat}-{day}-{slot}.html")
    if not force and os.path.exists(cache) and os.path.getsize(cache) > 20000:
        return open(cache, encoding="utf-8", errors="ignore").read(), "cache"

    html = ""
    try:
        html = fetch_http(url)
    except Exception as e:
        print("  · HTTP 失败", e)

    mode = "http"
    if looks_blocked(html):
        print("  · 触发校验，改用浏览器渲染")
        time.sleep(1.5)
        html = fetch_browser(url)
        mode = "browser"

    if not looks_blocked(html):
        with open(cache, "w", encoding="utf-8") as f:
            f.write(html)
    return html, mode


def clean_name(s):
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^[）、，,·\-\s]+", "", s)
    s = re.sub(r"[、，,·\-\s]+$", "", s)
    return s


def guess_brand(name):
    low = name.lower()
    for brand, keys in BRANDS:
        if any(k in low for k in keys):
            return brand
    return "其他"


def parse_items(html, cat_key):
    out = []
    for chunk in re.split(r'<li\s+data-follow-id="p', html)[1:]:
        pid = re.match(r"(\d+)", chunk)
        if not pid:
            continue
        pid = pid.group(1)

        m = re.search(r'<h3>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', chunk, re.S)
        if not m:
            continue
        href = m.group(1)
        name = clean_name(m.group(2).split("<span")[0])
        if not name or len(name) < 3:
            continue

        # 部分品类（如固态硬盘）是系列页，价格形如 359-1799，取区间下限
        mp = re.search(r'<b class="price-type">([\d,]+)(?:-([\d,]+))?</b>', chunk)
        if not mp:
            continue
        price = int(mp.group(1).replace(",", ""))
        if price < 20 or price > 60000:
            continue
        if any(b in name for b in BLOCK):
            continue

        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = "https://detail.zol.com.cn" + href

        out.append({
            "pid": pid, "name": name, "cat": cat_key,
            "brand": guess_brand(name), "price": price, "url": href,
        })
    return out


def write_log(entry):
    """把一次采集写入 data/collect-log.json（按时间倒序，最多 60 条）"""
    path = os.path.join(DATA_DIR, "collect-log.json")
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
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def current_slot():
    """18 点前算中午场（12），之后算晚间场（23）"""
    h = datetime.now(timezone(timedelta(hours=8))).hour
    return 12 if h < 18 else 23


def price_step(p):
    if p < 300:
        return 2
    if p < 800:
        return 5
    if p < 2000:
        return 10
    if p < 5000:
        return 20
    return 50


def build_series(pid, price, cat):
    """首次回溯：以真实当前价为终点，按品类波动向前模拟"""
    rng = random.Random(int(pid) * 7919)
    vol, drift = CAT_PARAMS.get(cat, (0.12, -0.03))
    daily = vol / (252 ** 0.5)
    levels = [1.0]
    for _ in range(DAYS - 1):
        levels.append(levels[-1] * (1 + drift / DAYS + rng.gauss(0, daily)))
    last = levels[-1]
    step = price_step(price)
    series = [max(1, int(round(price * lv / last / step) * step)) for lv in levels]
    series[-1] = price
    return series


def load_snapshots():
    """读取历史快照 -> {day: {pid: price}}
       文件名支持 YYYY-MM-DD.json 与 YYYY-MM-DD-12.json / -23.json；
       同一天有多场时，按场次升序依次覆盖，最终保留较晚一场。"""
    rows = []
    if not os.path.isdir(SNAP_DIR):
        return {}

    for fn in os.listdir(SNAP_DIR):
        m = re.match(r"^(\d{4}-\d{2}-\d{2})(?:-(\d{1,2}))?\.json$", fn)
        if not m:
            continue
        day, slot = m.group(1), int(m.group(2) or 0)
        try:
            d = json.load(open(os.path.join(SNAP_DIR, fn), encoding="utf-8"))
            items = {str(k): int(v) for k, v in d.get("items", {}).items()}
            rows.append((day, slot, items))
        except Exception:
            continue

    rows.sort(key=lambda x: (x[0], x[1]))
    snaps = {}
    for day, slot, items in rows:
        snaps.setdefault(day, {}).update(items)
    return snaps


def main():
    dry = "--dry" in sys.argv
    force = "--force" in sys.argv
    now = datetime.now(timezone(timedelta(hours=8)))
    day = now.strftime("%Y-%m-%d")
    slot = current_slot()

    t0 = time.time()
    cat_counts = {}
    all_items, seen = [], set()
    for key, label, url in CATS:
        html, mode = get_page(key, url, force)
        items = parse_items(html, key) if html else []
        cat_counts[label] = len(items)
        print(f"{label:<4} {len(items):>3} 条  [{mode}]")
        for it in items:
            if it["pid"] in seen:
                continue
            seen.add(it["pid"])
            all_items.append(it)
        if mode != "cache":
            time.sleep(2.4)

    dedup = {}
    for it in all_items:
        k = (it["cat"], re.sub(r"\s+", "", it["name"]).lower())
        if k not in dedup or it["price"] > dedup[k]["price"]:
            dedup[k] = it
    products = sorted(dedup.values(), key=lambda x: (x["cat"], -x["price"]))

    # 数据闸门：抓取量异常直接中止，避免把坏数据发上线
    if len(products) < MIN_PRODUCTS:
        print(f"\n! 仅抓到 {len(products)} 款（阈值 {MIN_PRODUCTS}），判定为被风控，中止。")
        print("  data.json 保持不变，线上仍显示上一场数据。")
        write_log({
            "time": now.isoformat(timespec="seconds"),
            "type": "price", "status": "fail", "slot": slot,
            "count": len(products), "duration": round(time.time() - t0, 1),
            "cats": cat_counts,
            "message": f"仅 {len(products)} 款，低于阈值 {MIN_PRODUCTS}，可能被风控，已中止发布"
        })
        sys.exit(1)

    # 先落本场快照，再读回，保证 realDays 从今天开始计算
    os.makedirs(SNAP_DIR, exist_ok=True)
    snap_path = os.path.join(SNAP_DIR, f"{day}-{slot}.json")
    snap_tmp = snap_path + ".tmp"
    with open(snap_tmp, "w", encoding="utf-8") as f:
        json.dump({"day": day, "slot": slot,
                   "items": {p["pid"]: p["price"] for p in products}},
                  f, ensure_ascii=False)
    os.replace(snap_tmp, snap_path)

    snaps = load_snapshots()
    snap_days = sorted(snaps.keys())
    dates = [(now - timedelta(days=DAYS - 1 - i)).strftime("%Y-%m-%d") for i in range(DAYS)]
    date_idx = {d: i for i, d in enumerate(dates)}

    for p in products:
        series = build_series(p["pid"], p["price"], p["cat"])
        for d in snap_days:
            if d in date_idx and p["pid"] in snaps[d]:
                series[date_idx[d]] = snaps[d][p["pid"]]
        series[-1] = p["price"]
        p["series"] = series
        p["realDays"] = sum(1 for d in snap_days if p["pid"] in snaps[d])

    payload = {
        "updated": now.isoformat(timespec="seconds"),
        "day": day,
        "slot": slot,
        "source": "中关村在线 (detail.zol.com.cn)",
        "count": len(products),
        "days": DAYS,
        "dates": dates,
        "realSince": snap_days[0] if snap_days else day,
        "products": products,
    }

    empty_cats = [k for k, v in cat_counts.items() if v == 0]
    status = "partial" if empty_cats else "ok"
    message = "抓取正常" if not empty_cats else ("以下品类无数据：" + "、".join(empty_cats))
    write_log({
        "time": now.isoformat(timespec="seconds"),
        "type": "price", "status": status, "slot": slot,
        "count": len(products), "duration": round(time.time() - t0, 1),
        "cats": cat_counts, "message": message
    })

    print(f"\n合计 {len(products)} 款 · 场次 {slot} 点 · 真实快照 {len(snap_days)} 天")

    if dry:
        for p in products[:12]:
            print(f"  {p['cat']:<10} {p['name'][:34]:<36} ¥{p['price']:<7} 序列{len(p['series'])}")
        return

    os.makedirs(DATA_DIR, exist_ok=True)
    out = os.path.join(DATA_DIR, "data.json")
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, out)
    print(f"已写入 data/data.json（{os.path.getsize(out)/1024:.0f} KB）")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        write_log({
            "time": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
            "type": "price", "status": "error", "slot": current_slot(),
            "count": 0, "duration": 0,
            "message": ("运行异常：" + str(exc))[:180]
        })
        raise
