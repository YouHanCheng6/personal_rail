"""Evidence parsing and deterministic comparison. No model-generated rail facts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

ROUTES = {
    "longchuan-beijing": ("龙川西", "北京"),
    "beijing-longchuan": ("北京", "龙川西"),
    "beijing-ganzhou": ("北京", "赣州"),
    "ganzhou-beijing": ("赣州", "北京"),
    "jieyangjichang-longchuan": ("揭阳机场站", "龙川西"),
    "longchuan-jieyangjichang": ("龙川西", "揭阳机场站"),
}
STATIONS = {
    "揭阳机场站": {"揭阳机场"},
    "龙川西": {"龙川西"},
    "北京": {"北京", "北京西", "北京南", "北京丰台", "北京北", "北京朝阳", "清河"},
    "赣州": {"赣州", "赣州西"},
}
HUBS = {"赣州西", "南昌西"}


def robots_allowed(text: str, url: str) -> bool:
    """RFC-style longest-match wildcard policy for our named crawler or wildcard."""
    groups, agents, rules = [], [], []
    for line in text.splitlines() + ["User-agent: END"]:
        line = line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (x.strip() for x in line.split(":", 1))
        if key.lower() == "user-agent":
            if rules:
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(value.lower())
        elif key.lower() in {"allow", "disallow"} and value:
            rules.append((key.lower(), value))
    exact = [r for a, r in groups if any(x in "personalrail" for x in a if x != "*")]
    chosen = exact or [r for a, r in groups if "*" in a]
    path = urlsplit(url).path + (
        "?" + urlsplit(url).query if urlsplit(url).query else ""
    )
    matches = []
    for rule_list in chosen:
        for kind, pattern in rule_list:
            end = pattern.endswith("$")
            regex = re.escape(pattern[:-1] if end else pattern).replace(r"\*", ".*")
            if re.match(regex + ("$" if end else ""), path):
                matches.append(
                    (len(pattern.replace("*", "").rstrip("$")), kind == "allow")
                )
    return max(matches)[1] if matches else True


def _leg(raw: dict, source_date: str, source_url: str, observed: str) -> dict | None:
    try:
        number = raw["trainNumber"]
        if not re.fullmatch(r"[GDCZTKLSY]?\d{1,5}", number):
            return None
        duration = int(raw["runTime"])
        if not 1 <= duration <= 4320:
            return None
        departure = datetime.fromisoformat(
            (raw.get("departureDate") or source_date) + "T" + raw["departureTime"]
        )
        arrival = departure + timedelta(minutes=duration)
        if arrival.strftime("%H:%M") != raw["arrivalTime"]:
            return None
        seats = []
        for s in raw.get("seatItemInfoList", []):
            name = s.get("seatName", "")
            if name not in {
                "二等座",
                "一等座",
                "商务座",
                "特等座",
                "硬座",
                "硬卧",
                "软卧",
                "二等卧",
                "一等卧",
                "高级软卧",
            }:
                continue
            price = s.get("price", s.get("showSeatPrice", s.get("seatPrice")))
            price = float(price) if price is not None else None
            if price is not None and (
                not math.isfinite(price) or not 0 < price <= 20000
            ):
                price = None
            seats.append({"name": name, "price": price})
        if not seats:
            seats = [{"name": "席别待核实", "price": None}]
        return {
            "train": number,
            "origin": raw["departureStationName"],
            "destination": raw["arrivalStationName"],
            "departure": departure.isoformat(timespec="minutes"),
            "arrival": arrival.isoformat(timespec="minutes"),
            "duration_minutes": duration,
            "seats": seats,
            "source_url": source_url,
            "observed_at": observed,
            "source_date": source_date,
            "train_type": "高速动车"
            if number.startswith("G")
            else ("动车" if number.startswith(("D", "C")) else "普速列车"),
            "fleet": "具体车型未核实",
            "fuxing_reference": bool(raw.get("isFuXingTrain")),
        }
    except (KeyError, ValueError, TypeError, OverflowError):
        return None


def parse_page(html: str, url: str, observed: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        raise ValueError("公开页面未提供可解析数据；可能需要交互验证。")
    try:
        state = json.loads(script.string)["props"]["pageProps"]["initialState"]
        day = date.fromisoformat(state["dDate"]).isoformat()
        trains = state["trainSearchInfo"]["trainInfoList"]
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("页面格式或参考日期发生变化，停止解析。") from exc
    direct = [v for t in trains[:150] if (v := _leg(t, day, url, observed))]
    transfers = []
    for item in state.get("transferSearchInfo", [])[:30]:
        raw = item.get("trainTransferInfos", [])
        if len(raw) == 2:
            legs = [_leg(t, day, url, observed) for t in raw]
            if all(legs):
                transfers.append(legs)
    return {
        "source_url": url,
        "observed_at": observed,
        "source_date": day,
        "direct": direct,
        "transfers": transfers,
    }


def choose_seat(leg: dict, policy: str) -> dict:
    long_trip = leg["duration_minutes"] >= 480
    order = {
        "economy": [
            "二等座",
            "硬座",
            "二等卧",
            "硬卧",
            "一等座",
            "软卧",
            "一等卧",
            "商务座",
        ],
        "balanced": (
            ["二等卧", "硬卧", "二等座", "硬座", "一等座", "软卧", "一等卧", "商务座"]
            if long_trip
            else [
                "二等座",
                "二等卧",
                "硬卧",
                "硬座",
                "一等座",
                "软卧",
                "一等卧",
                "商务座",
            ]
        ),
        "comfort": [
            "一等卧",
            "软卧",
            "一等座",
            "二等卧",
            "硬卧",
            "二等座",
            "硬座",
            "商务座",
        ],
    }[policy]
    s = min(
        leg["seats"], key=lambda x: order.index(x["name"]) if x["name"] in order else 99
    )
    return {**leg, "seat": s["name"], "fare": s["price"]}


def _candidate(
    legs: list[dict], wanted: date, seat: str, min_transfer: int, same_station: bool
) -> dict | None:
    if len(legs) == 2 and legs[0]["train"] == legs[1]["train"]:
        return None
    start, end = (
        datetime.fromisoformat(legs[0]["departure"]),
        datetime.fromisoformat(legs[-1]["arrival"]),
    )
    gap, cross, overnight = 0, False, False
    if len(legs) == 2:
        arrive = datetime.fromisoformat(legs[0]["arrival"])
        depart = datetime.fromisoformat(legs[1]["departure"])
        gap = int((depart - arrive).total_seconds() / 60)
        cross = legs[0]["destination"] != legs[1]["origin"]
        if (
            gap < (max(120, min_transfer) if cross else min_transfer)
            or gap > 480
            or (cross and same_station)
        ):
            return None
        # A stay crossing midnight or between midnight and 06:00 needs a separate warning.
        overnight = arrive.date() != depart.date() or arrive.hour < 6 or depart.hour < 6
    selected = [choose_seat(leg, seat) for leg in legs]
    prices = [leg["fare"] for leg in selected]
    fare = round(sum(prices), 2) if all(p is not None for p in prices) else None
    duration = int((end - start).total_seconds() / 60)
    if not 0 < duration <= 4320:
        return None
    comfort = []
    for leg in selected:
        base = {
            "商务座": 92,
            "一等卧": 90,
            "软卧": 86,
            "一等座": 82,
            "二等卧": 78,
            "硬卧": 72,
            "二等座": 66,
            "硬座": 42,
        }.get(leg["seat"], 45)
        if "卧" not in leg["seat"]:
            base -= min(25, max(0, leg["duration_minutes"] - 240) / 30)
        comfort.append(base)
    score = round(
        sum(c * leg["duration_minutes"] for c, leg in zip(comfort, selected))
        / sum(leg["duration_minutes"] for leg in selected)
        - (8 if len(legs) == 2 else 0)
        - (12 if overnight else 0)
    )
    risk = (
        "无换乘"
        if len(legs) == 1
        else (
            "较高"
            if cross or gap < 30 or overnight
            else ("中等" if gap < 45 else "较低")
        )
    )
    key = "|".join(
        f"{leg['train']}/{leg['origin']}/{leg['departure']}/{leg['destination']}/{leg['seat']}"
        for leg in selected
    )
    return {
        "id": hashlib.sha256(key.encode()).hexdigest()[:12],
        "legs": selected,
        "duration_minutes": duration,
        "rail_fare": fare,
        "total_cost_known": fare is not None and not cross and not overnight,
        "transfer_minutes": gap,
        "cross_station": cross,
        "overnight_transfer": overnight,
        "risk": risk,
        "comfort_score": max(0, min(100, score)),
        "comfort_label": "推测",
        "occupancy": "未知",
        "date_matches": start.date() == wanted,
        "source_date": start.date().isoformat(),
        "source_urls": sorted(set(leg["source_url"] for leg in legs)),
        "observed_at": min(leg["observed_at"] for leg in legs),
    }


def plan(
    snapshots: list[dict],
    route: str,
    wanted: date,
    seat: str,
    min_transfer: int,
    same_station: bool,
    preference: str,
    budget: float | None,
) -> list[dict]:
    origin, destination = ROUTES[route]
    starts, ends = STATIONS[origin], STATIONS[destination]
    all_legs = [leg for s in snapshots for leg in s["direct"]]
    sets = [
        [leg]
        for leg in all_legs
        if leg["origin"] in starts and leg["destination"] in ends
    ]
    sets += [
        ls
        for s in snapshots
        for ls in s["transfers"]
        if ls[0]["origin"] in starts and ls[-1]["destination"] in ends
    ]
    firsts = [
        leg
        for leg in all_legs
        if leg["origin"] in starts and leg["destination"] in HUBS
    ]
    lasts = [
        leg for leg in all_legs if leg["origin"] in HUBS and leg["destination"] in ends
    ]
    sets += [[a, b] for a in firsts for b in lasts if a["destination"] == b["origin"]]
    candidates = {}
    for legs in sets:
        p = _candidate(legs, wanted, seat, min_transfer, same_station)
        if p and (
            budget is None or (p["rail_fare"] is not None and p["rail_fare"] <= budget)
        ):
            candidates[p["id"]] = p
    result = list(candidates.values())
    for p in result:
        # Explicit heuristic: time in hours + fare/100 + transfers and fatigue penalties.
        risk_penalty = {"无换乘": 0, "较低": 1, "中等": 3, "较高": 7}[p["risk"]]
        p["comparison_score"] = round(
            p["duration_minutes"] / 60
            + (p["rail_fare"] / 100 if p["rail_fare"] is not None else 30)
            + risk_penalty
            + (100 - p["comfort_score"]) / 10,
            2,
        )
    key = {
        "balanced": lambda p: p["comparison_score"],
        "fastest": lambda p: p["duration_minutes"],
        "cheapest": lambda p: (
            p["rail_fare"] if p["rail_fare"] is not None else float("inf")
        ),
        "comfortable": lambda p: -p["comfort_score"],
    }[preference]
    result.sort(
        key=lambda p: (not p["date_matches"], key(p), p["duration_minutes"], p["id"])
    )
    return result[:60]
