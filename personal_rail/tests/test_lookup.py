import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from fastapi.testclient import TestClient

from personal_rail.lookup import SearchQuery, lookup, flight_destinations
from personal_rail.service import TZ
from personal_rail.tests.test_planner import snapshot, train


class Maps:
    async def resolve_hub(self, name):
        locations = {
            "梅州梅县机场": "116.09,24.26",
            "惠州平潭机场": "114.60,23.05",
            "揭阳潮汕国际机场": "116.50,23.55",
            "赣州黄金机场": "114.78,25.85",
            "广州白云国际机场": "113.30,23.39",
            "深圳宝安国际机场": "113.81,22.64",
        }
        return {"location": locations[name]}


class Source:
    def __init__(self):
        self.transport = self
        self.calls = []

    async def collect_pair(self, a, b, day):
        self.calls.append(("rail", a, b))
        return [
            snapshot(
                [
                    train(number=f"G{i}", origin=a, destination=b, day=str(day))
                    for i in range(1, 181)
                ],
                str(day),
            )
        ], []

    async def search(self, mode, a, b, day, **kwargs):
        self.calls.append((mode, a, b))
        return [
            {
                "mode": mode,
                "origin": a,
                "destination": b,
                "label": f"班次{i}",
                "price": 100 + i,
                "arrival": None,
                "missing": ["到达时间未知"],
            }
            for i in range(120)
        ], {"status": "fetched"}


def test_raw_lists_not_routes_or_top_forty_and_no_model_import():
    c = Source()
    q = SearchQuery(origin="北京", destination="龙川", departure_date="2026-10-07")
    updates = []
    r = asyncio.run(lookup(q, c, updates.append, Maps()))
    assert len(r["items"]["rail"]) == 360
    assert len(r["items"]["flight"]) > 120
    assert len(r["items"]["coach"]) == 120
    assert ("coach", "北京", "龙川") in c.calls
    assert not any(m == "rail" and a != "北京" for m, a, b in c.calls)
    assert r["complete"] and not r["pending"] and updates
    assert "candidates" not in r and "agent" not in r
    for name in ["agent.py", "journey.py", "assessment.py", "orchestrator.py"]:
        assert not (Path(__file__).parents[1] / name).exists()


def test_airports_near_longchuan_include_meizhou_and_huizhou_not_nanchang():
    target = {"city": "龙川", "location": (115.3, 24.1), "location_basis": "城市中心"}
    rows = asyncio.run(flight_destinations(target, 250, Maps()))
    names = {r["city"] for r in rows}
    assert {"梅州", "惠州", "揭阳"} <= names
    assert "南昌" not in names
    assert all(r["distance_km"] <= 250 for r in rows)


def test_api_rejects_old_planning_parameters_and_streams_rows(monkeypatch):
    import personal_rail.app as module

    async def fake(q, c, progress=None):
        result = {"items": {"rail": []}, "sources": [], "pending": [], "complete": True}
        if progress:
            progress(result)
        return result

    monkeypatch.setattr(module, "lookup", fake)
    q = {
        "origin": "北京",
        "destination": "龙川",
        "departure_date": str(datetime.now(TZ).date() + timedelta(days=1)),
    }
    with TestClient(module.app) as c:
        assert c.post("/api/plan", json=q).status_code == 404
        assert c.post("/api/search", json={**q, "budget": 500}).status_code == 422
        assert (
            c.post(
                "/api/search", json=q, headers={"origin": "https://evil.example"}
            ).status_code
            == 403
        )
        response = c.post("/api/search?stream=true", json=q)
        assert response.status_code == 200 and "event: result" in response.text


def test_cancel_stops_pending_source_tasks():
    async def run():
        started, stopped = asyncio.Event(), asyncio.Event()

        class Slow(Source):
            async def collect_pair(self, a, b, day):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

        task = asyncio.create_task(
            lookup(
                SearchQuery(
                    origin="北京",
                    destination="南昌",
                    departure_date="2026-10-01",
                    modes=["rail"],
                ),
                Slow(),
                maps=Maps(),
            )
        )
        await started.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert stopped.is_set()

    asyncio.run(run())


def test_failed_source_is_visible_not_false_no_service():
    class Broken(Source):
        async def collect_pair(self, *args):
            raise RuntimeError("private provider error")

    r = asyncio.run(
        lookup(
            SearchQuery(
                origin="北京",
                destination="南昌",
                departure_date="2026-10-01",
                modes=["rail"],
            ),
            Broken(),
            maps=Maps(),
        )
    )
    assert r["complete"] and not r["items"]["rail"]
    assert r["sources"][0]["status"] == "unavailable"
    assert "private" not in str(r)


def test_provider_transfers_preserved_without_extra_queries():
    class Transfers(Source):
        async def collect_pair(self, a, b, day):
            self.calls.append((a, b))
            first = train(number="D27", origin=a, destination="南昌西", day=str(day))
            second = train(number="G2771", origin="南昌西", destination=b, day=str(day))
            return [
                {
                    "direct": [],
                    "transfers": [snapshot([first, second], str(day))["direct"]],
                    "source_url": "https://example.com/route",
                    "source_date": str(day),
                    "observed_at": "2026-10-01T12:00",
                }
            ], [{"status": "fetched"}]

    c = Transfers()
    r = asyncio.run(
        lookup(
            SearchQuery(
                origin="北京",
                destination="龙川西",
                departure_date="2026-10-07",
                modes=["rail"],
            ),
            c,
            maps=Maps(),
        )
    )
    assert c.calls == [("北京", "龙川西")]
    row = r["items"]["rail"][0]
    assert row["kind"] == "transfer" and len(row["legs"]) == 2
    assert row["legs"][0]["train"] == "D27"
    assert row["source_url"] == "https://example.com/route"
    assert r["sources"][0]["transfer_count"] == 1


def test_duplicate_provider_itineraries_keep_both_sources():
    class Overlap(Source):
        async def collect_pair(self, a, b, day):
            snap = snapshot(
                [train(origin="北京", destination="龙川西", day=str(day))], str(day)
            )
            snap["direct"][0]["source_url"] = "https://example.com/" + b
            return [snap], []

    r = asyncio.run(
        lookup(
            SearchQuery(
                origin="北京",
                destination="龙川",
                departure_date="2026-10-07",
                modes=["rail"],
            ),
            Overlap(),
            maps=Maps(),
        )
    )
    assert len(r["items"]["rail"]) == 1
    assert len(r["items"]["rail"][0]["sources"]) == 2
