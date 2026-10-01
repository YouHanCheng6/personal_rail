"""Rail source facts, quick-query directions and crawler policy."""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from urllib.parse import urlsplit

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
            if not isinstance(name, str) or not name.strip():
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
