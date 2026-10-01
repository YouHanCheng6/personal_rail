"""Optional AMap adapter. Key stays server-side; no IP location inference."""

import os
from urllib.parse import urlencode

import httpx
from dotenv import dotenv_values

from .service import ROOT


class MapUnavailable(ValueError):
    pass


class Places:
    def __init__(self):
        self._hubs = {}
        self._details = {}

    def configured(self):
        return bool(
            os.environ.get("AMAP_WEB_KEY")
            or dotenv_values(ROOT / ".env").get("AMAP_WEB_KEY")
        )

    async def request(self, path, params):
        key = os.environ.get("AMAP_WEB_KEY") or dotenv_values(ROOT / ".env").get(
            "AMAP_WEB_KEY"
        )
        if not key:
            raise MapUnavailable("尚未配置地图服务，可先输入站名查询铁路。")
        async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
            response = await client.get(
                "https://restapi.amap.com/v3/" + path, params={**params, "key": key}
            )
            response.raise_for_status()
            data = response.json()
        if data.get("status") != "1":
            raise MapUnavailable("地图查询未成功，请检查Key权限和配额。")
        return data

    @staticmethod
    def public(p):
        return {
            "id": p["id"],
            "name": p["name"],
            "typecode": p.get("typecode", ""),
            "address": " ".join(
                str(p.get(k) or "") for k in ("pname", "cityname", "adname", "address")
            ),
            "location": p.get("location", ""),
            "city": p.get("cityname") or p.get("adname") or "",
            "city_code": p.get("citycode") or "",
            "map_url": "https://uri.amap.com/marker?"
            + urlencode(
                {
                    "position": p.get("location", ""),
                    "name": p["name"],
                    "typecode": p.get("typecode", ""),
                    "coordinate": "gaode",
                    "callnative": "0",
                }
            ),
        }

    async def search(self, keyword):
        data = await self.request(
            "place/text",
            {"keywords": keyword, "offset": 8, "page": 1, "extensions": "base"},
        )
        return [
            self.public(p)
            for p in data.get("pois", [])
            if p.get("id") and p.get("location")
        ]

    async def detail(self, id):
        if id in self._details:
            return self._details[id]
        data = await self.request("place/detail", {"id": id})
        pois = data.get("pois", [])
        if len(pois) != 1:
            raise MapUnavailable("地点未找到，请重新选择。")
        self._details[id] = self.public(pois[0])
        return self._details[id]

    async def resolve_hub(self, name):
        """Resolve evidence-named hubs only; no fuzzy POI substitution."""
        if name in self._hubs:
            return self._hubs[name]
        rows = await self.search(name)

        def hub_name(value):
            value = value.removesuffix("站")
            return value.replace("国际", "") if value.endswith("机场") else value

        exact = [
            p
            for p in rows
            if hub_name(p["name"]) == hub_name(name)
            and (
                not p.get("typecode")
                or set(p["typecode"].split("|")) & {"150104", "150200", "150300"}
            )
        ]
        if len(exact) != 1:
            raise MapUnavailable("车站或机场位置不能唯一核实")
        self._hubs[name] = exact[0]
        return exact[0]
