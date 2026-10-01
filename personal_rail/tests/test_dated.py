import json
from datetime import date, datetime

import pytest

from personal_rail.dated import parse_dated_page

URL = "https://train.qunar.com/train/tickets/beijing-to-ganzhou/2026-10-06"
OBSERVED = "2026-09-22T23:30:00+08:00"


def page(day="2026-10-06", depart="2026-10-06 23:00", arrive="2026-10-07 10:00"):
    meta = {"travelDate": day, "route": {"canonicalUrl": URL}}
    data = {
        "trains": {
            "encoding": "tuple_v1",
            "fields": [
                "no",
                "departStation",
                "arriveStation",
                "depart",
                "arrive",
                "seatStart",
                "seatLen",
            ],
            "seatFields": ["name", "price", "ticketCount"],
            "data": [["D27", "北京丰台", "赣州", depart, arrive, 0, 2]],
            "seatData": [["二等座", "372", 99], ["二等卧", "525", 0]],
        },
        "transfers": [],
    }
    return (
        '<script id="pageDataJSON" type="application/json">'
        + json.dumps(meta)
        + '</script><script id="pageData" type="application/json">'
        + json.dumps(data)
        + "</script>"
    )


def test_dated_parser_preserves_actual_dates_and_seat_prices():
    result = parse_dated_page(page(), URL, OBSERVED, date(2026, 10, 6))
    leg = result["direct"][0]
    assert leg["arrival"] == "2026-10-07T10:00"
    assert leg["duration_minutes"] == 660
    assert leg["seats"] == [
        {"name": "二等座", "price": 372.0},
        {"name": "二等卧", "price": 525.0},
    ]
    assert "ticketCount" not in json.dumps(result)


def test_dated_parser_rejects_source_date_mismatch():
    with pytest.raises(ValueError):
        parse_dated_page(page(day="2026-09-23"), URL, OBSERVED, date(2026, 10, 6))


def test_dated_parser_never_shifts_wrong_day_train():
    result = parse_dated_page(
        page(depart="2026-09-23 23:00", arrive="2026-09-24 10:00"),
        URL,
        OBSERVED,
        date(2026, 10, 6),
    )
    assert not result["direct"]


def test_dated_cache_is_date_scoped_and_expires(tmp_path, monkeypatch):
    import asyncio

    from personal_rail.service import Collector

    c = Collector(tmp_path)
    calls = []

    async def policy(client, base):
        return "User-agent: *\nAllow: /"

    async def get_text(client, url):
        calls.append(url)
        day = url.rsplit("/", 1)[-1]
        return page().replace("2026-10-06", day).replace("2026-10-07", day)

    monkeypatch.setattr(c, "policy", policy)
    monkeypatch.setattr(c, "get_text", get_text)
    # Use same-day valid legs; both dates are within the fixed clock's window.

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 22, 12, tzinfo=tz)

    monkeypatch.setattr("personal_rail.service.datetime", Clock)

    async def run():
        for day in [date(2026, 10, 6), date(2026, 10, 5), date(2026, 10, 6)]:
            rows, sources = await c.collect("beijing-ganzhou", day)
            assert rows[0]["source_date"] == day.isoformat()
        assert sources[0]["status"] == "cached"
        assert len(calls) == 2
        path = tmp_path / "dated-cache" / "beijing-ganzhou-2026-10-06.json"
        data = json.loads(path.read_text())
        data["fetched_epoch"] = 0
        path.write_text(json.dumps(data))
        await c.collect("beijing-ganzhou", date(2026, 10, 6))
        assert len(calls) == 3
        rows, sources = await c.collect("beijing-ganzhou", date(2026, 10, 7))
        assert not rows and sources[0]["status"] == "outside_presale"
        assert len(calls) == 3

    asyncio.run(run())


def test_dated_policy_denial_and_failure_never_use_old_cache(tmp_path, monkeypatch):
    import asyncio

    import httpx

    from personal_rail.service import Collector

    c = Collector(tmp_path)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 22, 12, tzinfo=tz)

    monkeypatch.setattr("personal_rail.service.datetime", Clock)

    async def policy(client, base):
        return "User-agent: *\nDisallow: /"

    async def forbidden(*args):
        raise AssertionError("must not fetch denied page")

    monkeypatch.setattr(c, "policy", policy)
    monkeypatch.setattr(c, "get_text", forbidden)
    assert not asyncio.run(c.collect("beijing-ganzhou", date(2026, 10, 6)))[0]

    async def allowed(client, base):
        return "User-agent: *\nAllow: /"

    async def failed(*args):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(c, "policy", allowed)
    monkeypatch.setattr(c, "get_text", failed)
    path = tmp_path / "dated-cache" / "beijing-ganzhou-2026-10-06.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps({"source_date": "2026-10-06", "source_url": URL, "fetched_epoch": 0})
    )
    rows, sources = asyncio.run(c.collect("beijing-ganzhou", date(2026, 10, 6)))
    assert rows == [] and sources[0]["status"] == "unavailable"


def test_dated_transfer_uses_leg_fares_and_actual_overnight_dates():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(page(), "html.parser")
    data = json.loads(soup.find("script", id="pageData").string)
    data["trains"]["data"] = []
    data["transfers"] = [
        {
            "totalPrice": "1",
            "legs": [
                {
                    "type": "TRAIN",
                    "trainNo": "G1",
                    "depStation": "北京西",
                    "arrStation": "南昌西",
                    "depTime": "2026-10-06 18:00",
                    "arrTime": "2026-10-06 23:30",
                    "seats": [{"name": "二等座", "price": 700}],
                },
                {
                    "type": "TRAIN",
                    "trainNo": "G2",
                    "depStation": "南昌西",
                    "arrStation": "赣州西",
                    "depTime": "2026-10-07 00:30",
                    "arrTime": "2026-10-07 02:30",
                    "seats": [{"name": "二等座", "price": 200}],
                },
            ],
        }
    ]
    soup.find("script", id="pageData").string = json.dumps(data)
    parsed = parse_dated_page(str(soup), URL, OBSERVED, date(2026, 10, 6))
    assert parsed["transfers"][0][0]["seats"][0]["price"] == 700
    assert parsed["transfers"][0][1]["departure"].startswith("2026-10-07T00:30")


def test_dated_canonical_route_must_match():
    with pytest.raises(ValueError):
        parse_dated_page(
            page().replace("beijing-to-ganzhou", "ganzhou-to-beijing"),
            URL,
            OBSERVED,
            date(2026, 10, 6),
        )


def test_every_source_train_is_retained_beyond_old_150_limit():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(page(), "html.parser")
    data = json.loads(soup.find("script", id="pageData").string)
    prototype = data["trains"]["data"][0]
    data["trains"]["data"] = [[f"D{i}", *prototype[1:]] for i in range(1, 181)]
    soup.find("script", id="pageData").string = json.dumps(data)
    result = parse_dated_page(str(soup), URL, OBSERVED, date(2026, 10, 6))
    assert len(result["direct"]) == 180
