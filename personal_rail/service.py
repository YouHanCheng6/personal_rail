"""Bounded public-source collection and a read-only gateway model adapter."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from dotenv import dotenv_values

from .planner import parse_page, robots_allowed
from .news import BASE as NEWS_BASE, FEEDS, parse_announcements
from .dated import BASE as DATED_BASE, PAGES as DATED_PAGES, parse_dated_page

ROOT = Path(__file__).resolve().parent
DATA = ROOT / ".local"
TZ = ZoneInfo("Asia/Shanghai")
MODEL_NAME = "自定义模型"
UA = "PersonalRail/1.0 (personal read-only timetable comparison)"
BASE = "https://trains.ctrip.com"
PAGES = {
    "jieyangjichang-longchuan": ["jieyangjichang-longchuan"],
    "longchuan-jieyangjichang": ["longchuan-jieyang"],
    "longchuan-beijing": [
        "longchuan-beijing",
        "longchuan-ganzhou",
        "ganzhou-beijing",
        "longchuan-nanchang",
        "nanchang-beijing",
    ],
    "beijing-longchuan": [
        "beijing-longchuan",
        "beijing-ganzhou",
        "ganzhou-longchuan",
        "beijing-nanchang",
        "nanchang-longchuan",
    ],
    "beijing-ganzhou": ["beijing-ganzhou", "beijing-nanchang", "nanchang-ganzhou"],
    "ganzhou-beijing": ["ganzhou-beijing", "ganzhou-nanchang", "nanchang-beijing"],
}
REFERENCES = [
    {
        "id": "presale",
        "title": "12306 · 客票预售期公告",
        "url": "https://www.12306.cn/mormhweb/zxdt/202205/t20220531_37508.html",
        "published": "2022-05-31",
        "kind": "官方规则",
        "note": "公告规定预售期15天（含当天）。起售时间和临时调整仍需以12306为准。",
        "marker": "15",
    },
    {
        "id": "fleet",
        "title": "12306 · 动车组车型与座席介绍",
        "url": "https://www.12306.cn/index/view/station/train_intro.html",
        "published": None,
        "kind": "车型资料",
        "note": "可查车型座席布置，但不能据此确认某日某车次的实际担当车型；车次字母不能确定CR型号。",
        "marker": "CR400",
    },
    {
        "id": "history",
        "title": "广州市政府 · 赣深高铁开通报道",
        "url": "https://www.gz.gov.cn/zwfw/zxfw/content/post_7958253.html",
        "published": "2021-12-10",
        "kind": "历史资料",
        "note": "报道记载赣深高铁于2021年12月10日开通，龙川西、赣州西在该线路上。历史旅行时间、票价不用于当前报价。",
        "marker": "赣深",
    },
]


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
        self.reference_lock = asyncio.Lock()
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

    async def page(self, client, slug: str, policy: str) -> tuple[dict | None, dict]:
        url = f"{BASE}/trainbooking/{slug}/"
        cached = _read(self.data / "cache" / f"{slug}.json")
        if not robots_allowed(policy, url):
            return None, {
                "url": url,
                "status": "unavailable",
                "message": "来源 robots 策略不允许读取；已跳过。",
            }
        if cached and time.time() - cached.get("fetched_epoch", 0) < 21600:
            return cached, {
                "url": url,
                "status": "cached",
                "observed_at": cached["observed_at"],
                "source_date": cached["source_date"],
            }
        try:
            html = await self.get_text(client, url)
            data = parse_page(html, url, now())
            data["fetched_epoch"] = time.time()
            _write(self.data / "cache" / f"{slug}.json", data)
            history_path = self.data / "observations" / f"{slug}.json"
            history = _read(history_path) or []
            history.append(
                {
                    "observed_at": data["observed_at"],
                    "source_date": data["source_date"],
                    "trains": [
                        {
                            "train": leg["train"],
                            "origin": leg["origin"],
                            "destination": leg["destination"],
                            "departure": leg["departure"],
                            "seats": leg["seats"],
                        }
                        for leg in data["direct"]
                    ],
                }
            )
            _write(history_path, history[-30:])
            return data, {
                "url": url,
                "status": "fetched",
                "observed_at": data["observed_at"],
                "source_date": data["source_date"],
            }
        except (httpx.HTTPError, ValueError, OSError):
            # Never relabel an old snapshot as freshly observed.
            if cached:
                return cached, {
                    "url": url,
                    "status": "stale",
                    "observed_at": cached["observed_at"],
                    "source_date": cached["source_date"],
                    "message": "本次读取失败，显示上次快照；不可视为当前时刻或票价。",
                }
            return None, {
                "url": url,
                "status": "unavailable",
                "message": "公开来源暂不可读或页面格式变化；未绕过访问限制。",
            }

    async def collect_reference(self, route: str):
        async with self.lock:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(18),
                headers={"User-Agent": UA},
                follow_redirects=False,
            ) as client:
                try:
                    policy = await self.policy(client, BASE)
                except (httpx.HTTPError, ValueError):
                    return [], [
                        {
                            "url": BASE + "/robots.txt",
                            "status": "unavailable",
                            "message": "无法核验来源访问策略，本次不发起页面采集。",
                        }
                    ]
                values = await asyncio.gather(
                    *(self.page(client, slug, policy) for slug in PAGES[route])
                )
            return [v[0] for v in values if v[0]], [v[1] for v in values]

    async def collect(self, route: str, wanted: date):
        """Only the requested date enters recommendations; no undated fallback."""
        slug = DATED_PAGES[route]
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

    async def news(self):
        async with self.news_lock:
            path = self.data / "news.json"
            cached = _read(path)
            if cached and 0 <= time.time() - cached.get("epoch", 0) < 900:
                return cached
            result = {
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
                        policy = await self.policy(client, NEWS_BASE)
                        if not robots_allowed(policy, url):
                            raise ValueError("policy")
                        html = await self.get_text(client, url)
                        items = parse_announcements(
                            html, url, label, datetime.now(TZ).date()
                        )
                        return items, {"title": label, "url": url, "status": "ok"}
                    except (httpx.HTTPError, ValueError):
                        return [], {"title": label, "url": url, "status": "unavailable"}

                values = await asyncio.gather(
                    *(read_feed(label, url) for label, url in FEEDS)
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

    async def references(self):
        async with self.reference_lock:
            path = self.data / "references.json"
            saved = _read(path)
            if saved and time.time() - saved["epoch"] < 86400:
                return saved["items"]
            result = []
            async with httpx.AsyncClient(
                timeout=12, headers={"User-Agent": UA}, follow_redirects=False
            ) as client:
                for ref in REFERENCES:
                    item = {k: v for k, v in ref.items() if k != "marker"}
                    try:
                        from urllib.parse import urlsplit

                        base = "https://" + urlsplit(ref["url"]).netloc
                        rules = await self.policy(client, base)
                        if not robots_allowed(rules, ref["url"]):
                            raise ValueError("policy")
                        html = await self.get_text(client, ref["url"])
                        if ref["marker"] not in html:
                            raise ValueError("content")
                        item.update({"status": "verified", "checked_at": now()})
                    except (httpx.HTTPError, ValueError):
                        item.update({"status": "link_only", "checked_at": None})
                    result.append(item)
            _write(path, {"epoch": time.time(), "items": result})
            return result

    def history(self, route):
        records = _read(self.data / "observations" / f"dated-{route}.json") or []
        return [
            {
                "route_page": DATED_PAGES[route],
                "url": records[-1]["url"]
                if records
                else f"{DATED_BASE}/train/tickets/{DATED_PAGES[route]}",
                "observations": records,
            }
        ]


def model_config() -> dict:
    env = {**dotenv_values(ROOT.parent / ".env"), **os.environ}
    url = env.get("RAIL_MODEL_BASE_URL", "").strip().rstrip("/")
    key = env.get("RAIL_MODEL_API_KEY", "").strip()
    model = env.get("RAIL_MODEL_NAME", "").strip()
    if not url.startswith("https://") or not key or not model:
        raise ValueError("请配置HTTPS模型接口、API密钥与模型名称。")
    return {"url": url, "key": key, "model": model}


REASONS = {
    "balanced": "按铁路耗时、席别参考价、换乘负担和预计舒适度综合取舍。",
    "time": "优先缩短站到站总耗时；换乘等待已计入。",
    "cost": "优先降低所列席别的铁路参考票价。",
    "comfort": "优先考虑席别与长时间乘坐的疲劳负担；舒适度仍是推测。",
    "direct": "少一次换乘，就少一次衔接的不确定性。",
    "buffer": "为换乘保留了缓冲时间，但不保证实际列车准点。",
}


async def analyze(candidates: list[dict], preference: str) -> dict:
    fallback = {
        "status": "fallback",
        "model": MODEL_NAME,
        "candidate_id": candidates[0]["id"] if candidates else None,
        "reasons": [
            REASONS[
                {
                    "balanced": "balanced",
                    "fastest": "time",
                    "cheapest": "cost",
                    "comfortable": "comfort",
                }[preference]
            ]
        ],
        "message": "模型暂不可用，已保留可复核的规则排序。",
    }
    if not candidates:
        return {
            **fallback,
            "status": "no_candidates",
            "message": "没有符合条件的公开参考方案。",
        }
    try:
        cfg = model_config()
        # Constrained advice: the model may select an existing route but cannot author facts.
        shortlist = candidates[:12]
        compact = [
            {
                "id": c["id"],
                "minutes": c["duration_minutes"],
                "fare": c["rail_fare"],
                "risk": c["risk"],
                "comfort_estimate": c["comfort_score"],
                "legs": len(c["legs"]),
                "date_matches": c["date_matches"],
                "total_cost_known": c["total_cost_known"],
            }
            for c in shortlist
        ]
        system = (
            "你是个人铁路行程比较Agent。只能根据给定候选做取舍，不能检索、编造事实或改变数值。"
            "真实上座率未知，舒适度为规则推测，票价为公开参考。优先满足用户排序偏好，"
            "优先选择date_matches=true；综合偏好可按时间、票价、换乘和舒适度权衡。"
            '仅返回JSON：{"candidate_id":"候选ID","reason_codes":["balanced"]}。'
            "reason_codes只允许balanced,time,cost,comfort,direct,buffer，最多3个。"
        )
        async with httpx.AsyncClient(timeout=55, follow_redirects=False) as client:
            response = await client.post(
                cfg["url"] + "/chat/completions",
                headers={"Authorization": "Bearer " + cfg["key"]},
                json={
                    "model": cfg["model"],
                    "messages": [
                        {"role": "system", "content": system},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {"preference": preference, "candidates": compact},
                                ensure_ascii=False,
                            ),
                        },
                    ],
                    "max_completion_tokens": 800,
                    "stream": False,
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        answer = json.loads(content)
        chosen = next(c for c in shortlist if c["id"] == answer["candidate_id"])
        if any(c["date_matches"] for c in shortlist) and not chosen["date_matches"]:
            raise ValueError("date mismatch")
        if preference != "balanced" and chosen["id"] != candidates[0]["id"]:
            # A preference is a constraint, not a suggestion that the model can override.
            raise ValueError("preference mismatch")
        codes = answer["reason_codes"]
        if (
            not isinstance(codes, list)
            or not 1 <= len(codes) <= 3
            or any(c not in REASONS for c in codes)
        ):
            raise ValueError("invalid explanation")
        if "direct" in codes and len(chosen["legs"]) != 1:
            raise ValueError("invalid direct claim")
        if "buffer" in codes and (
            len(chosen["legs"]) != 2 or chosen["transfer_minutes"] < 45
        ):
            raise ValueError("invalid buffer claim")
        return {
            "status": "ok",
            "model": MODEL_NAME,
            "candidate_id": chosen["id"],
            "reasons": [REASONS[c] for c in codes],
            "message": "模型仅在已采集方案中做取舍；时间、票价和风险说明均由程序核验。",
        }
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        AttributeError,
        StopIteration,
        httpx.HTTPError,
    ):
        return fallback
