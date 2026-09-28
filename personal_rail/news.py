"""Small official announcement index; deterministic, no model calls."""

import re
from datetime import date
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

BASE = "https://kyfw.12306.cn"
FEEDS = [
    ("12306最新动态", BASE + "/mormhweb/zxdt/index_zxdt.html"),
    ("北京局", BASE + "/mormhweb/1/6/index_fl.html"),
    ("南昌局", BASE + "/mormhweb/1/14/index_fl.html"),
    ("广州局", BASE + "/mormhweb/1/15/index_fl.html"),
]


def parse_announcements(html, source_url, publisher, today):
    items = {}
    soup = BeautifulSoup(html, "html.parser")
    for stamp in soup.select(".zxdt_time_in"):
        row = stamp.find_parent("li")
        anchor = row.find("a", href=True) if row else None
        match = re.search(r"\d{4}-\d{2}-\d{2}", stamp.get_text())
        if not anchor or not match:
            continue
        published = date.fromisoformat(match[0])
        if not 0 <= (today - published).days <= 60:
            continue
        url = urljoin(source_url, anchor["href"])
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.netloc != "kyfw.12306.cn"
            or not parts.path.startswith("/mormhweb/")
            or parts.query
        ):
            continue
        title = anchor.get_text(" ", strip=True)[:220]
        if title:
            items[url] = {
                "title": title,
                "url": url,
                "published": published.isoformat(),
                "publisher": publisher,
            }
    if not soup.select(".zxdt_time_in"):
        raise ValueError("公告列表结构不可识别")
    return sorted(
        items.values(), key=lambda x: (x["published"], x["url"]), reverse=True
    )[:8]
