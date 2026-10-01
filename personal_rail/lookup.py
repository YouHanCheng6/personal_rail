"""Independent transport facts. No agent, path search, scoring or booking."""

import asyncio
from datetime import date
from math import asin, cos, radians, sin, sqrt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .multimodal import AIR_CITIES, COACH_CITIES, PublicTransport
from .places import Places
from .rail_catalog import CITIES

# Approximate city centres are used only to discover nearby airport cities.
CENTRES = {
    "北京": (116.4, 39.9),
    "龙川": (115.3, 24.1),
    "赣州": (114.9, 25.8),
    "揭阳": (116.4, 23.5),
    "南昌": (115.9, 28.7),
    "广州": (113.3, 23.1),
    "深圳": (114.1, 22.6),
    "梅州": (116.1, 24.3),
    "惠州": (114.4, 23.1),
    "武汉": (114.3, 30.6),
    "郑州": (113.6, 34.7),
}
AIRPORTS = {
    "北京": "北京首都国际机场",
    "赣州": "赣州黄金机场",
    "揭阳": "揭阳潮汕国际机场",
    "南昌": "南昌昌北国际机场",
    "广州": "广州白云国际机场",
    "深圳": "深圳宝安国际机场",
    "梅州": "梅州梅县机场",
    "惠州": "惠州平潭机场",
    "武汉": "武汉天河国际机场",
    "郑州": "郑州新郑国际机场",
}
ALIASES = {"龙川西": "龙川", "揭阳机场": "揭阳", "梅州西": "梅州"}


class SearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    origin: str = Field(min_length=1, max_length=100)
    destination: str = Field(min_length=1, max_length=100)
    departure_date: date
    origin_id: str | None = None
    destination_id: str | None = None
    modes: list[Literal["rail", "flight", "coach"]] = Field(
        default_factory=lambda: ["rail", "flight", "coach"], min_length=1, max_length=3
    )
    airport_radius_km: int = Field(default=250, ge=50, le=600)


def km(a, b):
    x, y = map(radians, a)
    u, v = map(radians, b)
    return round(
        12742
        * asin(
            min(
                1, sqrt(sin((v - y) / 2) ** 2 + cos(y) * cos(v) * sin((u - x) / 2) ** 2)
            )
        )
    )


async def endpoint(name, place_id, maps):
    normalized = name.removesuffix("站")
    if normalized in CITIES or normalized in CENTRES:
        city = ALIASES.get(normalized, normalized)
        return {
            "name": name,
            "city": city,
            "rail": [normalized]
            if normalized in CITIES and normalized != "龙川"
            else ["龙川", "龙川西"]
            if city == "龙川"
            else [city],
            "location": CENTRES.get(city),
            "location_basis": "城市中心近似位置",
        }
    if not place_id:
        matches = await maps.search(name)
        exact = [p for p in matches if p["name"] == name]
        if len(exact) != 1:
            raise ValueError(
                "具体地点请点击查地点，并按地址选择；不能自动猜测同名地点。"
            )
        place = exact[0]
    else:
        place = await maps.detail(place_id)
        if place["name"] != name:
            raise ValueError("所选地点与名称不一致，请重新选择。")
    address = place.get("address", "")
    cities = [c for c in CENTRES if c + "县" in address or c + "市" in address]
    if not cities:
        raise ValueError("此地点不在当前已接入的区域目录内，请输入明确的城市或车站。")
    city = "龙川" if "龙川" in cities else cities[0]
    return {
        "name": name,
        "city": city,
        "rail": ["龙川", "龙川西"] if city == "龙川" else [city],
        "location": tuple(map(float, place["location"].split(","))),
        "location_basis": "所选地点",
        "map_url": place.get("map_url"),
    }


async def flight_destinations(target, radius, maps):
    city = target["city"]
    if city in AIR_CITIES:
        return [
            {
                "city": city,
                "airport": AIRPORTS[city],
                "distance_km": None,
                "distance_basis": "目的地所在城市；城市查询可能包含多个机场",
            }
        ]
    out = []
    for c in AIR_CITIES:
        if not target["location"] or c not in CENTRES:
            continue
        approx = km(target["location"], CENTRES[c])
        # Include a margin before exact airport geolocation, never invent road distance.
        if approx > radius + 80:
            continue
        distance = None
        try:
            airport = await maps.resolve_hub(AIRPORTS[c])
            distance = km(
                target["location"], tuple(map(float, airport["location"].split(",")))
            )
        except Exception:
            pass
        if (distance if distance is not None else approx) > radius:
            continue
        out.append(
            {
                "city": c,
                "airport": AIRPORTS[c],
                "distance_km": distance,
                "city_distance_km": approx,
                "distance_basis": f"机场至{target['location_basis']}的直线距离，非驾车里程"
                if distance is not None
                else "机场位置未核实；按城市中心距离纳入搜索范围",
            }
        )
    return sorted(
        out,
        key=lambda r: (
            r["distance_km"] if r["distance_km"] is not None else r["city_distance_km"]
        ),
    )


def rail_rows(snapshots):
    """Retain only provider-authored itineraries; never generate connections."""
    rows = []
    for snapshot in snapshots:
        rows.extend(
            {
                **r,
                "label": r["train"],
                "kind": "direct",
                "source_label": "去哪儿公开日期页",
                "mode": "rail",
            }
            for r in snapshot["direct"]
        )
        for legs in snapshot.get("transfers", []):
            if not legs:
                continue
            rows.append(
                {
                    "mode": "rail",
                    "kind": "transfer",
                    "legs": legs,
                    "label": " → ".join(r["train"] for r in legs),
                    "origin": legs[0]["origin"],
                    "destination": legs[-1]["destination"],
                    "departure": legs[0]["departure"],
                    "arrival": legs[-1]["arrival"],
                    "source_url": snapshot["source_url"],
                    "source_date": snapshot["source_date"],
                    "observed_at": snapshot["observed_at"],
                    "source_label": "去哪儿平台换乘方案",
                    "note": "来源平台提供的铁路换乘；逐段展示全部席别，不自行配对席别或保证换乘可行。",
                }
            )
    return rows


async def lookup(query, collector, progress=None, maps=None):
    maps = maps or Places()
    origin, target = await asyncio.gather(
        endpoint(query.origin, query.origin_id, maps),
        endpoint(query.destination, query.destination_id, maps),
    )
    if not hasattr(collector, "transport"):
        collector.transport = PublicTransport(collector)
    data = {
        "query": query.model_dump(mode="json"),
        "endpoints": {"origin": origin, "destination": target},
        "items": {m: [] for m in query.modes},
        "sources": [],
        "airports": [],
        "pending": [],
        "complete": False,
    }
    jobs = []
    if "rail" in query.modes:
        for a in origin["rail"]:
            for b in target["rail"]:
                if a in CITIES and b in CITIES and a != b:
                    jobs.append(("rail", a, b))
    if "flight" in query.modes:
        data["airports"] = await flight_destinations(
            target, query.airport_radius_km, maps
        )
        if origin["city"] in AIR_CITIES:
            jobs.extend(
                ("flight", origin["city"], p["city"])
                for p in data["airports"]
                if p["city"] != origin["city"]
            )
        else:
            data["sources"].append(
                {
                    "mode": "flight",
                    "status": "unsupported",
                    "message": "出发地不是已支持的航空城市，请改选出发城市查询。",
                }
            )
    if (
        "coach" in query.modes
        and origin["city"] in COACH_CITIES
        and target["city"] in COACH_CITIES
        and origin["city"] != target["city"]
    ):
        jobs.append(("coach", origin["city"], target["city"]))
    for mode in query.modes:
        if not any(j[0] == mode for j in jobs) and not any(
            s.get("mode") == mode for s in data["sources"]
        ):
            data["sources"].append(
                {
                    "mode": mode,
                    "status": "unsupported",
                    "message": "当前来源目录没有此查询范围；未据此判断没有班次。",
                }
            )
    jobs = list(dict.fromkeys(jobs))
    data["pending"] = [list(j) for j in jobs]

    def publish():
        if progress:
            progress(
                {
                    **data,
                    "items": {m: list(rows) for m, rows in data["items"].items()},
                    "sources": list(data["sources"]),
                    "pending": list(data["pending"]),
                }
            )

    publish()

    async def read(job):
        mode, a, b = job
        try:
            if mode == "rail":
                snapshots, sources = await collector.collect_pair(
                    a, b, query.departure_date
                )
                rows = rail_rows(snapshots)
            else:
                rows, source = await collector.transport.search(
                    mode, a, b, query.departure_date, wait_for_slot=True
                )
                sources = [source]
            if mode == "rail":
                sources = [
                    {
                        **s,
                        "returned_count": len(rows),
                        "direct_count": sum(r["kind"] == "direct" for r in rows),
                        "transfer_count": sum(r["kind"] == "transfer" for r in rows),
                        "message": f"取得 {sum(r['kind'] == 'direct' for r in rows)} 条直达、{sum(r['kind'] == 'transfer' for r in rows)} 条平台换乘方案；未自行拼接，不代表平台外全部班次。",
                    }
                    if s.get("status") in {"cached", "fetched"}
                    else s
                    for s in sources
                ]
            return (
                job,
                rows,
                [{**s, "mode": mode, "direction": f"{a} → {b}"} for s in sources],
            )
        except Exception:
            return (
                job,
                [],
                [
                    {
                        "mode": mode,
                        "direction": f"{a} → {b}",
                        "status": "unavailable",
                        "message": "来源未能读取；不能据此判定没有班次。",
                    }
                ],
            )

    tasks = [asyncio.create_task(read(j)) for j in jobs]
    try:
        for task in asyncio.as_completed(tasks):
            job, rows, sources = await task
            data["items"][job[0]].extend(rows)
            data["sources"].extend(sources)
            data["pending"].remove(list(job))
            publish()
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    # Merge identical timetable/seat facts from overlapping provider city pages.
    # All evidence links remain attached; different seat prices are never merged.
    import json

    def facts(value):
        if isinstance(value, dict):
            return {
                k: facts(v)
                for k, v in value.items()
                if k not in {"source_url", "source_label", "observed_at", "sources"}
            }
        if isinstance(value, list):
            return [facts(v) for v in value]
        return value

    for mode, rows in data["items"].items():
        unique = {}
        for row in rows:
            key = json.dumps(facts(row), sort_keys=True, ensure_ascii=False)
            evidence = {
                k: row.get(k) for k in ("source_url", "source_label", "observed_at")
            }
            if key not in unique:
                unique[key] = {**row, "sources": [evidence]}
            elif evidence not in unique[key]["sources"]:
                unique[key]["sources"].append(evidence)
        data["items"][mode] = list(unique.values())
    data["complete"] = True
    return data
