import re, time, urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
CATS = {
    "cpu": "https://detail.zol.com.cn/cpu/",
    "gpu": "https://detail.zol.com.cn/vga/",
    "mem": "https://detail.zol.com.cn/memory/",
}

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
    return raw.decode("gbk", errors="ignore")

ITEM = re.compile(
    r'<li data-follow-id="p(\d+)"[^>]*>.*?'
    r'<h3><a href="([^"]+)" title="([^"]+)"[^>]*>.*?</h3>.*?'
    r'<b class="price-type">([\d,]*)</b>',
    re.S)

for k, u in CATS.items():
    try:
        h = fetch(u)
        items = ITEM.findall(h)
        print(f"{k}: {len(items)} items")
        for it in items[:3]:
            print("   ", it[0], it[2], it[3] or "(no price)")
        time.sleep(0.6)
    except Exception as e:
        print(k, "ERR", e)
