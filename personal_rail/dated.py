"""Date-specific public HTML adapter. No private endpoints or inventory inference."""

import json
from datetime import date, datetime

from bs4 import BeautifulSoup

from .planner import _leg

BASE = "https://train.qunar.com"
PAGES = {
    "longchuan-beijing": "longchuanxi-to-beijing",
    "beijing-longchuan": "beijing-to-longchuanxi",
    "beijing-ganzhou": "beijing-to-ganzhou",
    "ganzhou-beijing": "ganzhou-to-beijing",
    "jieyangjichang-longchuan": "jieyangjichang-to-longchuanxi",
    "longchuan-jieyangjichang": "longchuanxi-to-jieyangjichang",
}


def _dated_leg(number, origin, destination, depart, arrive, seats, day, url, observed):
    try:
        start, end = datetime.fromisoformat(depart), datetime.fromisoformat(arrive)
        if start.tzinfo or end.tzinfo or start.second or end.second:
            return None
        duration = (end - start).total_seconds() / 60
        if duration != int(duration):
            return None
        if not all(
            isinstance(s, str) and 0 < len(s) <= 20 for s in [origin, destination]
        ):
            return None
        return _leg(
            {
                "trainNumber": number,
                "departureStationName": origin,
                "arrivalStationName": destination,
                "departureDate": start.date().isoformat(),
                "departureTime": start.strftime("%H:%M"),
                "arrivalTime": end.strftime("%H:%M"),
                "runTime": int(duration),
                "seatItemInfoList": [
                    {"seatName": s.get("name"), "price": s.get("price")} for s in seats
                ],
            },
            day,
            url,
            observed,
        )
    except (ValueError, TypeError, OverflowError):
        return None


def parse_dated_page(html: str, url: str, observed: str, wanted: date) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    try:
        meta = json.loads(soup.find("script", id="pageDataJSON").string)
        data = json.loads(soup.find("script", id="pageData").string)
        day = date.fromisoformat(meta["travelDate"]).isoformat()
        if day != wanted.isoformat() or meta["route"]["canonicalUrl"] != url:
            raise ValueError("来源返回日期或路线不符，未采用。")
        fresh = meta.get("freshness", {})
        if fresh.get("trainDetailAllowed") is False:
            raise ValueError("来源未提供有效的当日车次资料。")
        trains = data["trains"]
        if trains["encoding"] != "tuple_v1":
            raise ValueError("来源数据结构变化。")
        fields, seat_fields = trains["fields"], trains["seatFields"]
        if not {
            "no",
            "departStation",
            "arriveStation",
            "depart",
            "arrive",
            "seatStart",
            "seatLen",
        } <= set(fields):
            raise ValueError("来源车次字段不完整。")
        if not {"name", "price"} <= set(seat_fields):
            raise ValueError("来源席别字段不完整。")
        direct = []
        for values in trains["data"][:150]:
            raw = dict(zip(fields, values, strict=True))
            start, length = raw["seatStart"], raw["seatLen"]
            if (
                type(start) is not int
                or type(length) is not int
                or not 0 <= start <= len(trains["seatData"])
                or not 0 <= length <= 20
                or start + length > len(trains["seatData"])
            ):
                continue
            seats = [
                dict(zip(seat_fields, row, strict=True))
                for row in trains["seatData"][start : start + length]
            ]
            leg = _dated_leg(
                raw["no"],
                raw["departStation"],
                raw["arriveStation"],
                raw["depart"],
                raw["arrive"],
                seats,
                day,
                url,
                observed,
            )
            if leg and leg["departure"][:10] == day:
                direct.append(leg)
        transfers = []
        for item in data.get("transfers", [])[:30]:
            rows = item.get("legs", [])
            if len(rows) != 2 or any(r.get("type") != "TRAIN" for r in rows):
                continue
            legs = [
                _dated_leg(
                    r["trainNo"],
                    r["depStation"],
                    r["arrStation"],
                    r["depTime"],
                    r["arrTime"],
                    r.get("seats", []),
                    day,
                    url,
                    observed,
                )
                for r in rows
            ]
            if all(legs) and legs[0]["departure"][:10] == day:
                transfers.append(legs)
        return {
            "source_url": url,
            "source_date": day,
            "observed_at": observed,
            "direct": direct,
            "transfers": transfers,
        }
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("指定日期页面不可解析或日期不符；未采用。") from exc
