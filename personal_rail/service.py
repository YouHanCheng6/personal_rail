"""Public railway and official-news collection."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from .dated import BASE as DATED_BASE
from .dated import PAGES as DATED_PAGES
from .dated import parse_dated_page
from .news import CATEGORY_FEEDS, parse_category
from .planner import robots_allowed

ROOT = Path(__file__).resolve().parent
DATA = ROOT / ".local"
TZ = ZoneInfo("Asia/Shanghai")
UA = "PersonalRail/1.0 (personal read-only timetable comparison)"


def now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def _read(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temp.chmod(0o600)
    temp.replace(path)


class Collector:
    def __init__(self, data: Path = DATA):
        self.data = data
        self.lock = asyncio.Lock()
        self.news_lock = asyncio.Lock()
        self.semaphore = asyncio.Semaphore(2)
        self.policies: dict = {}

    async def get_text(self, client: httpx.AsyncClient, url: str) -> str:
        # URLs are exclusively constructed from the constants above, never user/model input.
        async with self.semaphore:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                if response.is_redirect:
                    raise ValueError("来源要求跳转或交互；已停止，未绕过验证。")
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 2_000_000:
                        raise ValueError("来源响应超出2MB上限。")
                    chunks.append(chunk)
                return b"".join(chunks).decode("utf-8", errors="replace")

    async def policy(self, client, base: str) -> str:
        cached = self.policies.get(base)
        if cached and time.time() - cached[0] < 21600:
            return cached[1]
        try:
            rules = await self.get_text(client, base + "/robots.txt")
            if "<html" in rules.lower():
                raise ValueError("无法识别来源访问策略。")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in {404, 410}:
                raise
            rules = ""
        self.policies[base] = (time.time(), rules)
        return rules

    async def collect(self, route: str, wanted: date):
        """Only the requested date is returned; no undated fallback."""
        return await self._collect_page(route, DATED_PAGES[route], wanted)

    async def collect_pair(self, origin, destination, wanted):
        from .rail_catalog import CITIES

        if origin not in CITIES or destination not in CITIES or origin == destination:
            raise ValueError("未支持的线路端点")
        slug = CITIES[origin] + "-to-" + CITIES[destination]
        return await self._collect_page("pair-" + slug, slug, wanted)

    async def _collect_page(self, route, slug, wanted):
        url = f"{DATED_BASE}/train/tickets/{slug}/{wanted.isoformat()}"
        base_info = {
            "url": url,
            "provider": "去哪儿",
            "requested_date": wanted.isoformat(),
        }
        today = datetime.now(TZ).date()
        if not today <= wanted <= today + timedelta(days=14):
            return [], [
                {
                    **base_info,
                    "status": "outside_presale",
                    "message": "所选日期超出通常15天预售期，暂不采集或推定当天车次。",
                }
            ]
        async with self.lock:
            cache_path = (
                self.data / "dated-cache" / f"{route}-{wanted.isoformat()}.json"
            )
            cached = _read(cache_path)
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(18),
                headers={"User-Agent": UA},
                follow_redirects=False,
            ) as client:
                try:
                    policy = await self.policy(client, DATED_BASE)
                    if not robots_allowed(policy, url):
                        return [], [
                            {
                                **base_info,
                                "status": "unavailable",
                                "message": "来源访问规则不允许读取；已停止。",
                            }
                        ]
                    if (
                        cached
                        and cached.get("schema") == 3
                        and cached.get("source_date") == wanted.isoformat()
                        and cached.get("source_url") == url
                        and 0 <= time.time() - cached.get("fetched_epoch", 0) < 600
                    ):
                        return [cached], [
                            {
                                **base_info,
                                "status": "cached",
                                "source_date": cached["source_date"],
                                "observed_at": cached["observed_at"],
                            }
                        ]
                    html = await self.get_text(client, url)
                    data = parse_dated_page(html, url, now(), wanted)
                    data["fetched_epoch"] = time.time()
                    _write(cache_path, data)
                    history_path = self.data / "observations" / f"dated-{route}.json"
                    history = _read(history_path) or []
                    history.append(
                        {
                            "observed_at": data["observed_at"],
                            "source_date": data["source_date"],
                            "url": url,
                            "direct_count": len(data["direct"]),
                            "transfer_count": len(data["transfers"]),
                        }
                    )
                    _write(history_path, history[-30:])
                    # A bounded cache: retain at most 30 queried dates per route.
                    files = sorted(
                        cache_path.parent.glob(f"{route}-*.json"),
                        key=lambda p: p.stat().st_mtime,
                        reverse=True,
                    )
                    for old in files[30:]:
                        old.unlink()
                    return [data], [
                        {
                            **base_info,
                            "status": "fetched",
                            "source_date": data["source_date"],
                            "observed_at": data["observed_at"],
                        }
                    ]
                except (httpx.HTTPError, ValueError, OSError):
                    return [], [
                        {
                            **base_info,
                            "status": "unavailable",
                            "message": "未取得该日可核实的公开资料，可能是来源不可用、格式或日期不符；未使用旧日期或过期缓存替代。",
                        }
                    ]

    async def news(self, category="rail"):
        async with self.news_lock:
            path = self.data / (
                "news.json" if category == "rail" else f"news-{category}.json"
            )
            cached = _read(path)
            if (
                cached
                and cached.get("schema") == 2
                and 0 <= time.time() - cached.get("epoch", 0) < 900
            ):
                return cached
            result = {
                "schema": 2,
                "epoch": time.time(),
                "checked_at": now(),
                "items": [],
                "sources": [],
            }
            async with httpx.AsyncClient(
                timeout=12, headers={"User-Agent": UA}, follow_redirects=False
            ) as client:

                async def read_feed(label, url):
                    try:
                        from urllib.parse import urlsplit

                        policy = await self.policy(
                            client, "https://" + urlsplit(url).netloc
                        )
                        if not robots_allowed(policy, url):
                            raise ValueError("policy")
                        html = await self.get_text(client, url)
                        items = parse_category(
                            html, url, label, datetime.now(TZ).date(), category
                        )
                        return items, {"title": label, "url": url, "status": "ok"}
                    except (httpx.HTTPError, ValueError):
                        return [], {"title": label, "url": url, "status": "unavailable"}

                values = await asyncio.gather(
                    *(read_feed(label, url) for label, url in CATEGORY_FEEDS[category])
                )
            unique = {}
            for items, source in values:
                result["sources"].append(source)
                for item in items:
                    unique[item["url"]] = item
            result["items"] = sorted(
                unique.values(), key=lambda x: (x["published"], x["url"]), reverse=True
            )[:8]
            _write(path, result)
            return result
