from __future__ import annotations

import asyncio
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .planner import ROUTES, plan
from .service import ROOT, TZ, Collector, MODEL_NAME, REFERENCES, analyze, now

app = FastAPI(
    title="沿线 · 个人铁路 Agent", docs_url=None, redoc_url=None, openapi_url=None
)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"],
)
collector = Collector()
results = {}
analysis_locks = {}
rate = []


@app.middleware("http")
async def local_boundary(request: Request, call_next):
    if request.method not in {"GET", "HEAD"}:
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if origin and origin != expected:
            return JSONResponse({"detail": "仅允许本机同源请求。"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "仅允许本机同源请求。"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    response.headers["Cache-Control"] = "no-store"
    return response


class Query(BaseModel):
    route: Literal[
        "longchuan-beijing",
        "beijing-longchuan",
        "beijing-ganzhou",
        "ganzhou-beijing",
        "jieyangjichang-longchuan",
        "longchuan-jieyangjichang",
    ]
    departure_date: date
    preference: Literal["balanced", "fastest", "cheapest", "comfortable"] = "balanced"
    seat: Literal["balanced", "economy", "comfort"] = "balanced"
    min_transfer: int = Field(default=45, ge=20, le=180)
    same_station: bool = True
    budget: float | None = Field(default=None, gt=0, le=20000, allow_inf_nan=False)


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/meta")
async def meta():
    today = datetime.now(TZ).date()
    return {
        "routes": [
            {"id": k, "origin": v[0], "destination": v[1]} for k, v in ROUTES.items()
        ],
        "today": today.isoformat(),
        "default_date": (today + timedelta(days=1)).isoformat(),
        "presale_last_date": (today + timedelta(days=14)).isoformat(),
        "model": MODEL_NAME,
        "references": [
            {k: v for k, v in r.items() if k != "marker"} for r in REFERENCES
        ],
    }


@app.post("/api/plan")
async def create_plan(query: Query):
    today = datetime.now(TZ).date()
    if not today <= query.departure_date <= today + timedelta(days=365):
        raise HTTPException(422, "请选择今天起一年内的日期。")
    stamp = time.monotonic()
    rate[:] = [t for t in rate if stamp - t < 60]
    if len(rate) >= 10:
        raise HTTPException(429, "查询过于频繁，请一分钟后重试。")
    rate.append(stamp)
    snapshots, sources = await collector.collect(query.route, query.departure_date)
    candidates = plan(
        snapshots,
        query.route,
        query.departure_date,
        query.seat,
        query.min_transfer,
        query.same_station,
        query.preference,
        query.budget,
    )
    candidates = [candidate for candidate in candidates if candidate["date_matches"]]
    # For a same-day search, never recommend a train that already departed.
    if query.departure_date == today:
        current = datetime.now(TZ).replace(tzinfo=None)
        candidates = [
            candidate
            for candidate in candidates
            if not candidate["date_matches"]
            or datetime.fromisoformat(candidate["legs"][0]["departure"]) > current
        ]
    token = uuid.uuid4().hex
    data = {
        "id": token,
        "created_at": now(),
        "query": query.model_dump(mode="json"),
        "candidates": candidates,
        "data_status": "available"
        if candidates
        else (
            "outside_presale"
            if (query.departure_date - today).days >= 15
            else ("no_matching_options" if snapshots else "source_unavailable")
        ),
        "sale_date": (query.departure_date - timedelta(days=14)).isoformat(),
        "sources": sources,
        "presale_open": (query.departure_date - today).days < 15,
        "occupancy": "未知：未取得真实上座率，余票不等于车内人数。",
        "scope": "仅含公开来源覆盖的直达及一次换乘方案；不保证穷尽。全程为站到站，铁路票价为成人参考价，未含市内交通、餐饮、住宿及服务费。",
        "model": MODEL_NAME,
        "references": [
            {k: v for k, v in r.items() if k != "marker"} for r in REFERENCES
        ],
        "analysis": None,
    }
    for key in list(results):
        if stamp - results[key][0] > 3600:
            results.pop(key, None)
            analysis_locks.pop(key, None)
    if len(results) >= 30:
        oldest = next(iter(results))
        results.pop(oldest)
        analysis_locks.pop(oldest, None)
    results[token] = (stamp, data)
    return data


@app.post("/api/analyze/{plan_id}")
async def agent_analysis(plan_id: str):
    if plan_id not in results:
        raise HTTPException(404, "行程已过期，请重新查询。")
    lock = analysis_locks.setdefault(plan_id, asyncio.Lock())
    async with lock:
        data = results[plan_id][1]
        if data["analysis"] is None:
            data["analysis"] = await analyze(
                data["candidates"], data["query"]["preference"]
            )
        return data["analysis"]


@app.get("/api/history/{route}")
async def history(route: str):
    if route not in ROUTES:
        raise HTTPException(404, "不支持该方向。")
    return {"items": collector.history(route)}


@app.get("/api/references")
async def references():
    return {"items": await collector.references()}


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/api/news")
async def official_news():
    return await collector.news()
