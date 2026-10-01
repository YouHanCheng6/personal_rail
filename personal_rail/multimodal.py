"""Read-only public flight/coach pages. No booking APIs or executable page code."""

import asyncio
import hashlib
import json
import math
import re
import time
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from .planner import robots_allowed
from .rail_catalog import CITIES
from .service import UA, TZ, _read, _write

AIR_CITIES = {
    "北京": "PEK",
    "梅州": "MXZ",
    "惠州": "HUZ",
    "南昌": "KHN",
    "赣州": "KOW",
    "揭阳": "SWA",
    "广州": "CAN",
    "深圳": "SZX",
    "武汉": "WUH",
    "郑州": "CGO",
}
COACH_CITIES = {
    k: v
    for k, v in CITIES.items()
    if k not in {"龙川西", "揭阳机场", "梅州西", "潮汕", "衡水"}
}
COACH_CITIES.update({"梅州": "meizhou", "普宁": "puning", "河源": "heyuan"})


def read_nuxt(script):
    """Decode the provider's literal serialization, NEVER evaluate JavaScript."""
    match = re.fullmatch(
        r"window\.__NUXT__=\(function\(([^)]*)\)\{return (.*)\}\((.*)\)\);?",
        script.strip(),
        re.S,
    )
    if not match:
        raise ValueError("来源结构改变")
    # Only JSON primitive arguments and the serializer's undefined sentinel are accepted.
    token_re = (
        r'"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|[A-Za-z_$][\w$]*|[^\s]'
    )
    args = re.findall(token_re, match[3])
    normalized, i = [], 0
    while i < len(args):
        if args[i : i + 2] == ["void", "0"]:
            normalized.append("null")
            i += 2
        else:
            normalized.append(args[i])
            i += 1
    values = json.loads("[" + "".join(normalized) + "]")
    names = match[1].split(",")
    if len(names) != len(values) or any(isinstance(v, (dict, list)) for v in values):
        raise ValueError("来源常量无效")
    env = dict(zip(names, values))
    tokens = re.findall(token_re, match[2])
    out = []
    for i, token in enumerate(tokens):
        if re.fullmatch(r"[A-Za-z_$][\w$]*", token):
            if tokens[i + 1 : i + 2] == [":"]:
                token = json.dumps(token)
            elif token in env:
                token = json.dumps(env[token])
            elif token not in {"true", "false", "null"}:
                raise ValueError("页面包含非数据表达式")
        out.append(token)
    return json.loads("".join(out))


def amount(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and 0 < result <= 100000 else None
    except (TypeError, ValueError):
        return None


def parse_flights(html, origin, destination, wanted, url, observed):
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", string=re.compile(r"^window\.__NUXT__="))
    if not script:
        raise ValueError("未取得航班资料")
    state = read_nuxt(script.text)["state"]["book1"]
    if (
        state.get("DepartureDate") != wanted.isoformat()
        or state.get("FlyOffCity") != origin
        or state.get("ArriveCity") != destination
    ):
        raise ValueError("航班日期或城市不匹配")
    visible = {}
    for card in soup.select(".flight-item-head"):
        name, price = (
            card.select_one(".flight-item-name"),
            card.select_one(".head-prices strong em"),
        )
        if name:
            code = re.search(r"([A-Z0-9]{2}\d{3,4})$", name.get_text(strip=True))
            if code:
                visible[code[1]] = amount(
                    price.get_text(strip=True).replace("¥", "").replace(",", "")
                    if price
                    else None
                )
    offers = []
    for row in state.get("flightLists", []):
        code = row.get("flightNo")
        price = amount(row.get("flightPrice"))
        if code not in visible:
            continue
        if price != visible[code]:
            price = None
        departure = datetime.fromisoformat(row["flyOffTime"])
        arrival = datetime.fromisoformat(row["arrivalTime"])
        if (
            departure.date() != wanted
            or arrival <= departure
            or arrival - departure > timedelta(days=1)
        ):
            continue
        if not row.get("oapname") or not row.get("aapname"):
            continue
        offers.append(
            {
                "mode": "flight",
                "coverage": "partial",
                "provider_flag": state.get("dataflag", "unknown"),
                "origin": row["oapname"],
                "destination": row["aapname"],
                "origin_city": origin,
                "destination_city": destination,
                "departure": departure.isoformat(),
                "arrival": arrival.isoformat(),
                "label": code,
                "price": price,
                "price_complete": False,
                "source_url": url,
                "source_label": "同程公开航班页",
                "observed_at": observed,
                "source_date": wanted.isoformat(),
                "missing": (["票价未公布或未核实"] if price is None else [])
                + ["含税及服务费总价未核实"],
                "note": "来源只展示部分航班，不代表全部或最低价。公开展示起价；税费、行李额及可售状态未核实，不能当作完整含税报价。",
            }
        )
    return offers


def parse_coaches(html, origin, destination, wanted, url, observed):
    soup = BeautifulSoup(html, "html.parser")
    data = soup.select_one("#__NEXT_DATA__")
    if not data:
        raise ValueError("未取得客运资料")
    state = json.loads(data.text)["props"]["pageProps"]["initialState"]

    def matches(value, city):
        return isinstance(value, str) and (
            value == COACH_CITIES[city]
            or value.removesuffix("市").removesuffix("县") == city
        )

    if not matches(state.get("fromPy"), origin) or not matches(
        state.get("toPy"), destination
    ):
        raise ValueError("客运线路不匹配")
    if not isinstance(state.get("lines"), list):
        raise ValueError("客运班次字段缺失")
    offers = []
    for row in state.get("lines", []):
        missing = []
        day = row.get("fromDate")
        departure = None
        if day and re.fullmatch(r"\d{2}:\d{2}", row.get("fromTime") or ""):
            departure = datetime.fromisoformat(day + "T" + row["fromTime"])
        if not departure or day != wanted.isoformat():
            missing.append("来源日期不符，不能移作所选日期班次")
        station = row.get("toStationName") or ""
        if not station or station.removesuffix("县").removesuffix(
            "市"
        ) == destination.removesuffix("县").removesuffix("市"):
            missing.append("具体下车站未公布")
        minutes = amount(row.get("useMinutes"))
        arrival = (
            departure + timedelta(minutes=minutes)
            if departure and minutes and minutes <= 4320
            else None
        )
        if arrival is None:
            missing.append("到达时间未公布")
        price = amount(row.get("fullPrice"))
        if price is None:
            missing.append("票价未公布")
        if not row.get("fromStationName"):
            missing.append("上车站未公布")
        offers.append(
            {
                "mode": "coach",
                "origin": row.get("fromStationName") or origin,
                "destination": station or destination,
                "origin_city": origin,
                "destination_city": destination,
                "departure": departure.isoformat() if departure else None,
                "arrival": arrival.isoformat() if arrival else None,
                "label": row.get("busNumber") or "客车",
                "price": price,
                "price_complete": False,
                "source_url": url,
                "source_label": "携程公开汽车时刻页",
                "observed_at": observed,
                "source_date": day,
                "missing": missing + ["服务费及可售状态未核实"],
                "note": "保留页面真实日期和车站，不推测到站、不平移班次。",
            }
        )
    return offers


class PublicTransport:
    def __init__(self, collector):
        self.collector = collector
        self.locks = {}
        self.folder = collector.data / "transport-cache"

    async def search(
        self,
        mode,
        origin,
        destination,
        wanted,
        *,
        wait_for_slot=False,
        reference_time=None,
    ):
        catalog = AIR_CITIES if mode == "flight" else COACH_CITIES
        if (
            mode not in {"flight", "coach"}
            or origin not in catalog
            or destination not in catalog
            or origin == destination
        ):
            raise ValueError("不支持的交通方式或城市")
        today = datetime.now(TZ).date()
        if not today <= wanted <= today + timedelta(days=365):
            raise ValueError("日期超限")
        url = (
            f"https://www.ly.com/flights/itinerary/oneway/{catalog[origin]}-{catalog[destination]}?date={wanted}"
            if mode == "flight"
            else f"https://bus.ctrip.com/schedule/{catalog[origin]}-{catalog[destination]}"
        )
        base = f"https://{urlsplit(url).netloc}"
        source = {
            "url": url,
            "provider": "同程" if mode == "flight" else "携程汽车",
            "mode": mode,
            "requested_date": str(wanted),
        }
        key = hashlib.sha256(
            f"{mode}:{origin}:{destination}:{wanted}".encode()
        ).hexdigest()[:24]
        async with self.locks.setdefault(base, asyncio.Lock()):
            async with httpx.AsyncClient(
                timeout=25, headers={"User-Agent": UA}, follow_redirects=False
            ) as client:
                try:
                    rules = await self.collector.policy(client, base)
                    if not robots_allowed(rules, url):
                        return [], {
                            **source,
                            "status": "blocked",
                            "message": "来源规则不允许读取该页面。",
                        }
                    cached = _read(self.folder / (key + ".json"))
                    if (
                        cached
                        and cached.get("schema") == 4
                        and cache_usable(cached["at"], time.time(), reference_time)
                    ):
                        return cached["offers"], {
                            **source,
                            "status": "cached",
                            "message": coach_message(cached["offers"], wanted)
                            if mode == "coach"
                            else "复用本次任务开始时10分钟内的来源快照；保留实际读取时间，非实时成交或最低价保证。"
                            if reference_time is not None
                            and time.time() - cached["at"] >= 600
                            else "公开资料已读取（10分钟缓存）；来源覆盖不完整，非最低价或可售保证。",
                            "coverage": "partial",
                            "returned_count": len(cached["offers"]),
                        }
                    # Persistent shared cooldown also survives a process restart.
                    cooldown = self.folder / (mode + "-cooldown.json")
                    previous = _read(cooldown) or {}
                    delays = [
                        float(x)
                        for x in re.findall(
                            r"(?im)^Crawl-delay:\s*(\d+(?:\.\d+)?)", rules
                        )
                    ]
                    delay = max([2] + delays)
                    remaining = previous.get("at", 0) + delay - time.time()
                    if wait_for_slot and 0 < remaining <= 120:
                        # Wait remains cancellable when the user cancels the query.
                        while remaining > 0:
                            await asyncio.sleep(min(remaining, 60))
                            remaining = previous.get("at", 0) + delay - time.time()
                    if remaining > 0:
                        return [], {
                            **source,
                            "status": "rate_limited",
                            "retry_after": math.ceil(remaining),
                            "message": f"遵守来源抓取间隔，约{math.ceil(remaining)}秒后可再查询；本次未取到新数据。",
                        }
                    _write(cooldown, {"at": time.time()})
                    rendered = None
                    if mode == "flight" and (self.collector.data / "browsers").exists():
                        from .rendered_source import read_rendered

                        rendered = await read_rendered(url)
                        html = rendered["initial_html"]
                    else:
                        html = await self.collector.get_text(client, url)
                    observed = (
                        datetime.now(TZ)
                        .replace(tzinfo=None)
                        .isoformat(timespec="seconds")
                    )
                    parser = parse_flights if mode == "flight" else parse_coaches
                    offers = parser(html, origin, destination, wanted, url, observed)
                    render_note = ""
                    if rendered is not None:
                        try:
                            from .rendered_source import parse_rendered

                            offers = parse_rendered(
                                rendered,
                                offers,
                                origin,
                                destination,
                                wanted,
                                url,
                                observed,
                            )
                        except (ValueError, KeyError, TypeError):
                            render_note = "动态列表核验失败，仅保留初始部分航班。"
                    page_complete = bool(offers) and all(
                        o.get("coverage") == "page_complete" for o in offers
                    )
                    _write(
                        self.folder / (key + ".json"),
                        {"schema": 4, "at": time.time(), "offers": offers},
                    )
                    for old in sorted(
                        self.folder.glob("*.json"),
                        key=lambda p: p.stat().st_mtime,
                        reverse=True,
                    )[100:]:
                        if not old.name.endswith("-cooldown.json"):
                            old.unlink(missing_ok=True)
                    return offers, {
                        **source,
                        "status": ("reference" if offers else "reference_empty")
                        if mode == "coach"
                        else ("fetched" if offers else "empty"),
                        "message": coach_message(offers, wanted)
                        if mode == "coach"
                        else f"读取{len(offers)}条公开资料；"
                        + (
                            "动态页面列出航班已核对，不代表全网或最低成交价。"
                            if page_complete
                            else "仅部分来源资料，不能据此断言最低价。" + render_note
                        ),
                        "coverage": "partial",
                        "returned_count": len(offers),
                        "page_complete": page_complete,
                        "observed_at": observed,
                    }
                except httpx.HTTPStatusError as exc:
                    return [], {
                        **source,
                        "status": "unavailable",
                        "error_code": "http_status",
                        "message": f"来源返回 HTTP {exc.response.status_code}，未取得班次；不是无班次。",
                    }
                except httpx.TimeoutException:
                    return [], {
                        **source,
                        "status": "unavailable",
                        "error_code": "timeout",
                        "message": "来源访问超时，未取得班次；可稍后重试。",
                    }
                except (
                    httpx.HTTPError,
                    ValueError,
                    KeyError,
                    TypeError,
                    OSError,
                    RecursionError,
                ):
                    return [], {
                        **source,
                        "status": "unavailable",
                        "error_code": "source_read_failed",
                        "message": "来源网络或数据校验失败，未取得可用资料；不是无班次。",
                    }


def cache_usable(observed, now, reference_time=None):
    if 0 <= now - observed < 600:
        return True
    return (
        reference_time is not None
        and 0 <= now - reference_time <= 1260
        and 0 <= reference_time - observed < 600
    )


def coach_message(offers, wanted):
    count = sum(r.get("source_date") == wanted.isoformat() for r in offers)
    return (
        f"公开时刻页取得 {len(offers)} 条资料，其中 {count} 条日期与所选日期相同。"
        if offers
        else "公开时刻页当前返回空列表。"
    ) + "此入口未提供指定日期查询，不能据此确认所选日期有票或无车。"
