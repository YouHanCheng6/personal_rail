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


CATEGORY_FEEDS = {
    "rail": FEEDS,
    "flight": [("中国民航局", "https://www.caac.gov.cn/XWZX/MHYW/")],
    "coach": [("交通运输部", "https://www.mot.gov.cn/")],
}


def parse_category(html, url, publisher, today, category):
    if category == "rail":
        return [
            {**item, "category": category}
            for item in parse_announcements(html, url, publisher, today)
        ]
    hosts = {
        "flight": {"www.caac.gov.cn"},
        "coach": {"www.mot.gov.cn", "xxgk.mot.gov.cn"},
    }[category]
    keywords = (
        r"航班|机场|旅客|运输保障|行李|退票|退改"
        if category == "flight"
        else r"公路|道路客运|道路运输|班线|汽车客运|客运班车|客运站|客车|收费站|自驾"
    )
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("li")
    dated_rows = 0
    items = {}
    for row in rows:
        stamp = row.select_one(".n_date, .date")
        anchor = row.find("a", href=True)
        match = re.search(r"\d{4}-\d{2}-\d{2}", stamp.get_text()) if stamp else None
        if not match or not anchor:
            continue
        dated_rows += 1
        try:
            published = date.fromisoformat(match[0])
        except ValueError:
            continue
        target = urljoin(url, anchor["href"])
        parts = urlsplit(target)
        if (
            parts.hostname not in hosts
            or parts.scheme not in {"http", "https"}
            or parts.username
            or parts.query
        ):
            continue
        target = parts._replace(scheme="https").geturl()
        title = anchor.get("title") or anchor.get_text(" ", strip=True)
        if (
            0 <= (today - published).days <= 60
            and re.search(keywords, title)
            and not re.search(
                r"会见|慰问|固定资产|运输量|客运量|决算|达标车型|技术规范", title
            )
        ):
            items[target] = {
                "title": title[:220],
                "url": target,
                "published": published.isoformat(),
                "publisher": publisher,
                "category": category,
            }
    if not dated_rows:
        raise ValueError("公告栏目结构不可识别")
    return sorted(items.values(), key=lambda x: x["published"], reverse=True)[:8]
