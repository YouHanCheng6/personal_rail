import json
from datetime import date

import pytest

from personal_rail.planner import parse_page, plan, robots_allowed


def train(
    number="G1",
    origin="龙川西",
    destination="赣州西",
    departure="23:10",
    duration=90,
    seats=None,
    day="2026-09-23",
):
    minutes = int(departure[:2]) * 60 + int(departure[3:]) + duration
    return {
        "trainNumber": number,
        "departureStationName": origin,
        "arrivalStationName": destination,
        "departureTime": departure,
        "arrivalTime": f"{minutes // 60 % 24:02}:{minutes % 60:02}",
        "runTime": duration,
        "departureDate": day,
        "seatItemInfoList": seats or [{"seatName": "二等座", "price": 100}],
        "takeDays": minutes // 1440,
    }


def page(trains, day="2026-09-23", transfers=None):
    data = {
        "props": {
            "pageProps": {
                "initialState": {
                    "dDate": day,
                    "trainSearchInfo": {"trainInfoList": trains},
                    "transferSearchInfo": transfers or [],
                }
            }
        }
    }
    return (
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(data)
        + "</script>"
    )


def snapshot(trains, day="2026-09-23", transfers=None):
    return parse_page(
        page(trains, day, transfers),
        "https://trains.ctrip.com/trainbooking/longchuan-beijing/",
        "2026-09-22T12:00:00+08:00",
    )


def test_overnight_connection_and_sum():
    a = snapshot([train()])
    b = snapshot(
        [train("G2", "赣州西", "北京西", "01:30", 480, day="2026-09-24")],
        day="2026-09-24",
    )
    result = plan(
        [a, b],
        "longchuan-beijing",
        date(2026, 9, 23),
        "balanced",
        45,
        True,
        "balanced",
        None,
    )
    # Adjacent-date pages can only join actual dated legs, never repeat a daily timetable.
    p = result[0]
    assert (
        p["duration_minutes"] == 620
        and p["transfer_minutes"] == 50
        and p["rail_fare"] == 200
    )
    assert p["legs"][1]["departure"].startswith("2026-09-24")
    assert (
        p["occupancy"] == "未知"
        and p["risk"] == "较高"
        and p["overnight_transfer"] is True
    )


def test_dont_project_another_day_into_future():
    a = snapshot([train(destination="北京西", duration=500, departure="10:00")])
    result = plan(
        [a],
        "longchuan-beijing",
        date(2026, 10, 1),
        "balanced",
        45,
        True,
        "balanced",
        None,
    )
    assert result[0]["date_matches"] is False
    assert result[0]["legs"][0]["departure"].startswith("2026-09-23")


def test_longchuan_is_not_longchuanxi():
    a = snapshot([train(origin="龙川", destination="北京西")])
    assert (
        plan(
            [a],
            "longchuan-beijing",
            date(2026, 9, 23),
            "balanced",
            45,
            True,
            "balanced",
            None,
        )
        == []
    )


def test_fare_is_selected_seat_not_source_total_or_standing():
    a = snapshot(
        [
            train(
                destination="北京西",
                seats=[
                    {"seatName": "无座", "price": 50},
                    {"seatName": "二等座", "price": 120},
                    {"seatName": "一等座", "price": 200},
                ],
            )
        ]
    )
    p = plan(
        [a],
        "longchuan-beijing",
        date(2026, 9, 23),
        "comfort",
        45,
        True,
        "balanced",
        None,
    )[0]
    assert p["rail_fare"] == 200 and p["legs"][0]["seat"] == "一等座"


def test_missing_fare_is_not_zero_and_budget_excludes_unknown():
    a = snapshot([train(destination="北京西", seats=[{"seatName": "二等座"}])])
    p = plan(
        [a],
        "longchuan-beijing",
        date(2026, 9, 23),
        "balanced",
        45,
        True,
        "balanced",
        None,
    )[0]
    assert p["rail_fare"] is None
    assert (
        plan(
            [a],
            "longchuan-beijing",
            date(2026, 9, 23),
            "balanced",
            45,
            True,
            "balanced",
            500,
        )
        == []
    )


def test_cross_station_is_never_a_free_connection():
    transfer = {
        "trainTransferInfos": [
            train(destination="南昌西", departure="08:00", duration=60),
            train("G2", "南昌", "北京西", "12:00", 500),
        ],
        "totalPrice": "1",
    }
    a = snapshot([], transfers=[transfer])
    assert (
        plan(
            [a],
            "longchuan-beijing",
            date(2026, 9, 23),
            "balanced",
            45,
            True,
            "balanced",
            None,
        )
        == []
    )
    p = plan(
        [a],
        "longchuan-beijing",
        date(2026, 9, 23),
        "balanced",
        45,
        False,
        "balanced",
        None,
    )[0]
    assert p["total_cost_known"] is False and p["risk"] == "较高"
    assert p["rail_fare"] == 200


def test_tight_connections_and_repeated_same_train_excluded():
    a = snapshot(
        [
            train(departure="08:00", duration=60),
            train("G2", "赣州西", "北京西", "09:15", 480),
        ]
    )
    assert (
        plan(
            [a],
            "longchuan-beijing",
            date(2026, 9, 23),
            "balanced",
            45,
            True,
            "balanced",
            None,
        )
        == []
    )


def test_malformed_or_contradictory_page_fails_closed():
    with pytest.raises(ValueError):
        parse_page("<html>captcha</html>", "x", "now")
    bad = train()
    bad["arrivalTime"] = "12:00"
    assert snapshot([bad])["direct"] == []


def test_robots_wildcards_query_and_most_specific():
    rules = "User-agent: *\nDisallow: /*?*\nDisallow: /private/*\nAllow: /"
    assert robots_allowed(
        rules, "https://trains.ctrip.com/trainbooking/beijing-ganzhou/"
    )
    assert not robots_allowed(
        rules, "https://trains.ctrip.com/trainbooking/beijing-ganzhou/?date=2026-09-23"
    )
    assert not robots_allowed(rules, "https://trains.ctrip.com/private/data")


def test_sort_priority_and_selected_date_are_stable():
    a = snapshot(
        [
            train(
                "G10",
                destination="北京西",
                departure="08:00",
                duration=500,
                seats=[{"seatName": "二等座", "price": 1000}],
            ),
            train(
                "D10",
                destination="北京西",
                departure="09:00",
                duration=800,
                seats=[{"seatName": "二等座", "price": 500}],
            ),
        ]
    )
    cheapest = plan(
        [a],
        "longchuan-beijing",
        date(2026, 9, 23),
        "economy",
        45,
        True,
        "cheapest",
        None,
    )
    fastest = plan(
        [a],
        "longchuan-beijing",
        date(2026, 9, 23),
        "economy",
        45,
        True,
        "fastest",
        None,
    )
    assert cheapest[0]["legs"][0]["train"] == "D10"
    assert fastest[0]["legs"][0]["train"] == "G10"
    old = snapshot(
        [
            train(
                "G11",
                destination="北京西",
                departure="08:00",
                duration=60,
                day="2026-09-22",
            )
        ],
        day="2026-09-22",
    )
    assert plan(
        [old, a],
        "longchuan-beijing",
        date(2026, 9, 23),
        "economy",
        45,
        True,
        "fastest",
        None,
    )[0]["date_matches"]


@pytest.mark.parametrize("reverse", [False, True])
def test_airport_routes_match_exact_railway_stations(reverse):
    pairs = [("揭阳机场", "龙川西"), ("揭阳", "龙川西"), ("揭阳机场", "龙川")]
    if reverse:
        pairs = [(b, a) for a, b in pairs]
    data = snapshot(
        [train(f"D{i + 1}", a, b, "10:00", 90) for i, (a, b) in enumerate(pairs)]
    )
    route = "longchuan-jieyangjichang" if reverse else "jieyangjichang-longchuan"
    candidates = plan(
        [data], route, date(2026, 9, 23), "balanced", 45, True, "balanced", None
    )
    assert len(candidates) == 1
    assert candidates[0]["legs"][0]["train"] == "D1"
    assert candidates[0]["occupancy"] == "未知"
