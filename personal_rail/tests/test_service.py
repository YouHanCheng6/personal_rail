import asyncio
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from personal_rail.app import app, collector, results, rate
from personal_rail.service import Collector, TZ, analyze
from personal_rail.tests.test_planner import snapshot, train


@pytest.fixture
def client(monkeypatch):
    rate.clear()
    results.clear()
    today = datetime.now(TZ).date().isoformat()

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(today + "T12:00:00").replace(tzinfo=tz)

    monkeypatch.setattr("personal_rail.app.datetime", FixedClock)

    async def collect(route, wanted):
        return [
            snapshot(
                [
                    train(
                        destination="北京西", departure="18:00", duration=500, day=today
                    )
                ],
                day=today,
            )
        ], []

    monkeypatch.setattr(collector, "collect", collect)
    return TestClient(app)


def test_scope_validation_and_cross_site(client):
    data = {
        "route": "shanghai-beijing",
        "departure_date": datetime.now(TZ).date().isoformat(),
    }
    assert client.post("/api/plan", json=data).status_code == 422
    data["route"] = "longchuan-beijing"
    assert (
        client.post(
            "/api/plan", json=data, headers={"Origin": "https://untrusted.example"}
        ).status_code
        == 403
    )
    assert client.get("/api/meta", headers={"Host": "evil.example"}).status_code == 400


def test_query_has_no_credentials_and_unknown_occupancy(client):
    res = client.post(
        "/api/plan",
        json={
            "route": "longchuan-beijing",
            "departure_date": datetime.now(TZ).date().isoformat(),
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["candidates"][0]["occupancy"] == "未知"
    assert "api_key" not in res.text and "Authorization" not in res.text
    assert data["candidates"][0]["date_matches"]


def test_date_range_and_presale(client):
    today = datetime.now(TZ).date()
    assert (
        client.post(
            "/api/plan",
            json={
                "route": "longchuan-beijing",
                "departure_date": str(today - timedelta(days=1)),
            },
        ).status_code
        == 422
    )
    data = client.post(
        "/api/plan",
        json={
            "route": "longchuan-beijing",
            "departure_date": str(today + timedelta(days=15)),
        },
    ).json()
    assert not data["presale_open"] and not data["candidates"]


def test_analysis_is_idempotent(client, monkeypatch):
    calls = []

    async def fake(candidates, preference):
        calls.append(1)
        return {"status": "ok"}

    monkeypatch.setattr("personal_rail.app.analyze", fake)
    id = client.post(
        "/api/plan",
        json={
            "route": "longchuan-beijing",
            "departure_date": datetime.now(TZ).date().isoformat(),
        },
    ).json()["id"]
    client.post("/api/analyze/" + id)
    client.post("/api/analyze/" + id)
    assert len(calls) == 1


def test_stale_cache_keeps_original_timestamp(tmp_path, monkeypatch):
    data = snapshot([train()])
    data["fetched_epoch"] = 0
    path = tmp_path / "cache"
    path.mkdir()
    (path / "longchuan-beijing.json").write_text(json.dumps(data))
    c = Collector(tmp_path)

    async def broken(*args):
        raise ValueError("blocked")

    monkeypatch.setattr(c, "get_text", broken)
    value, status = asyncio.run(
        c.page(None, "longchuan-beijing", "User-agent: *\nAllow: /")
    )
    assert value["observed_at"] == data["observed_at"] and status["status"] == "stale"
    assert not (tmp_path / "observations").exists()


def test_robots_denial_does_not_request_or_return_cache(tmp_path, monkeypatch):
    c = Collector(tmp_path)

    async def no_request(*args):
        raise AssertionError("must not request")

    monkeypatch.setattr(c, "get_text", no_request)
    value, status = asyncio.run(
        c.page(None, "longchuan-beijing", "User-agent: *\nDisallow: /")
    )
    assert value is None and status["status"] == "unavailable"


def test_model_missing_config_falls_back_without_inventing_route(monkeypatch):
    monkeypatch.setenv("RAIL_MODEL_NAME", "")
    result = asyncio.run(analyze([{"id": "known"}], "balanced"))
    assert result["status"] == "fallback" and result["candidate_id"] == "known"


def test_invalid_gateway_payload_falls_back(monkeypatch):
    from personal_rail.planner import plan
    from datetime import date

    candidates = plan(
        [snapshot([train(destination="北京西")])],
        "longchuan-beijing",
        date(2026, 9, 23),
        "balanced",
        45,
        True,
        "balanced",
        None,
    )
    monkeypatch.setattr(
        "personal_rail.service.model_config",
        lambda: {
            "url": "https://gateway.example/v1",
            "key": "test-only",
            "model": "test",
        },
    )

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": []}

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr("personal_rail.service.httpx.AsyncClient", Client)
    result = asyncio.run(analyze(candidates, "balanced"))
    assert result["status"] == "fallback"
    assert result["candidate_id"] == candidates[0]["id"]


def test_model_cannot_recommend_invented_candidate(monkeypatch):
    from personal_rail.planner import plan
    from datetime import date

    candidates = plan(
        [snapshot([train(destination="北京西")])],
        "longchuan-beijing",
        date(2026, 9, 23),
        "balanced",
        45,
        True,
        "balanced",
        None,
    )
    monkeypatch.setattr(
        "personal_rail.service.model_config",
        lambda: {
            "url": "https://gateway.example/v1",
            "key": "test-only",
            "model": "test",
        },
    )

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"candidate_id":"invented", "reason_codes":["direct"]}'
                        }
                    }
                ]
            }

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr("personal_rail.service.httpx.AsyncClient", Client)
    assert asyncio.run(analyze(candidates, "balanced"))["status"] == "fallback"


def test_departed_trains_not_recommended_for_today(client, monkeypatch):
    today = datetime.now(TZ).date().isoformat()

    async def collect(route, wanted):
        return [
            snapshot(
                [
                    train(
                        destination="北京西", departure="00:00", duration=500, day=today
                    )
                ],
                day=today,
            )
        ], []

    monkeypatch.setattr(collector, "collect", collect)
    data = client.post(
        "/api/plan", json={"route": "longchuan-beijing", "departure_date": today}
    ).json()
    assert data["candidates"] == []


@pytest.mark.parametrize(
    "route", ["jieyangjichang-longchuan", "longchuan-jieyangjichang"]
)
def test_airport_routes_are_exposed_and_queryable(client, monkeypatch, route):
    from personal_rail.service import PAGES

    assert route in {r["id"] for r in client.get("/api/meta").json()["routes"]}
    assert PAGES[route]
    today = datetime.now(TZ).date().isoformat()
    origin, destination = ("揭阳机场", "龙川西")
    if route.startswith("longchuan"):
        origin, destination = destination, origin

    async def collect(selected, wanted):
        assert selected == route
        return [
            snapshot(
                [
                    train(
                        origin=origin,
                        destination=destination,
                        departure="18:00",
                        day=today,
                    )
                ],
                day=today,
            )
        ], []

    monkeypatch.setattr(collector, "collect", collect)
    res = client.post("/api/plan", json={"route": route, "departure_date": today})
    assert res.status_code == 200
    assert len(res.json()["candidates"]) == 1


def test_mismatched_dates_never_enter_api_recommendations(client):
    wanted = (datetime.now(TZ).date() + timedelta(days=1)).isoformat()
    data = client.post(
        "/api/plan", json={"route": "longchuan-beijing", "departure_date": wanted}
    ).json()
    assert data["candidates"] == []
    advice = client.post("/api/analyze/" + data["id"]).json()
    assert not advice.get("candidate_id")
