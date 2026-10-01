"""Loopback-only transport information API. No model or planning runtime."""

import asyncio
import json
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .lookup import SearchQuery, lookup
from .places import Places, MapUnavailable
from .planner import ROUTES
from .service import ROOT, TZ, Collector

app = FastAPI(title="沿线 · 交通查询", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"],
)
collector = Collector()
slots = asyncio.Semaphore(2)


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


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/meta")
async def meta():
    return {
        "default_date": str(datetime.now(TZ).date() + timedelta(days=1)),
        "agent_enabled": False,
        "routes": [{"origin": a, "destination": b} for a, b in ROUTES.values()],
    }


@app.post("/api/search")
async def search(query: SearchQuery, stream: bool = False):
    today = datetime.now(TZ).date()
    if not today <= query.departure_date <= today + timedelta(days=365):
        raise HTTPException(422, "请选择今天起一年内的日期。")
    if slots.locked():
        raise HTTPException(429, "已有两个查询正在执行，请先取消或等待完成。")

    async def execute(report=None):
        async with slots:
            return await lookup(query, collector, progress=report)

    if not stream:
        try:
            return await execute()
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    async def events():
        queue = asyncio.Queue(maxsize=1)

        def report(value):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(value)

        task = asyncio.create_task(execute(report))
        try:
            while not task.done():
                waiter = asyncio.create_task(queue.get())
                try:
                    done, _ = await asyncio.wait(
                        {task, waiter}, timeout=10, return_when=asyncio.FIRST_COMPLETED
                    )
                    if waiter in done:
                        yield (
                            "event: progress\ndata: "
                            + json.dumps(waiter.result(), ensure_ascii=False)
                            + "\n\n"
                        )
                    elif task not in done:
                        yield ": heartbeat\n\n"
                finally:
                    if not waiter.done():
                        waiter.cancel()
                    with suppress(asyncio.CancelledError):
                        await waiter
            yield (
                "event: result\ndata: "
                + json.dumps(await task, ensure_ascii=False)
                + "\n\n"
            )
        except Exception as exc:
            message = (
                str(exc)
                if isinstance(exc, ValueError)
                else "查询未完成，请重试；已显示资料保留。"
            )
            yield (
                "event: error\ndata: "
                + json.dumps({"message": message}, ensure_ascii=False)
                + "\n\n"
            )
        finally:
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
    )


@app.get("/api/places")
async def places(q: str):
    if not 1 <= len(q.strip()) <= 100:
        raise HTTPException(422, "地点名称应为1–100个字符")
    try:
        return {"items": await Places().search(q)}
    except (MapUnavailable, httpx.HTTPError, ValueError):
        return {"items": [], "message": "地点搜索暂不可用，请使用城市或车站名称。"}


@app.get("/api/news")
async def news(category: Literal["rail", "flight", "coach"] = "rail"):
    return await collector.news(category)


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
