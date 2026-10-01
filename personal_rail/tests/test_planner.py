from personal_rail.planner import _leg, robots_allowed


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


def snapshot(trains, day="2026-09-23", transfers=None):
    url = "https://train.qunar.com/train/tickets/longchuanxi-to-beijing/" + day
    return {
        "source_date": day,
        "source_url": url,
        "observed_at": "2026-09-22T12:00:00+08:00",
        "direct": [
            leg
            for raw in trains
            if (leg := _leg(raw, day, url, "2026-09-22T12:00:00+08:00"))
        ],
        "transfers": transfers or [],
    }


def test_overnight_dates_are_preserved():
    leg = snapshot([train()])["direct"][0]
    assert leg["arrival"] == "2026-09-24T00:40"


def test_fare_and_seat_are_selected_together():
    leg = snapshot(
        [
            train(
                seats=[
                    {"seatName": "无座", "price": 50},
                    {"seatName": "二等座", "price": 120},
                    {"seatName": "一等座", "price": 200},
                ]
            )
        ]
    )["direct"][0]
    assert {s["name"]: s["price"] for s in leg["seats"]} == {
        "无座": 50,
        "二等座": 120,
        "一等座": 200,
    }


def test_missing_price_stays_unknown():
    leg = snapshot([train(seats=[{"seatName": "二等座"}])])["direct"][0]
    assert leg["seats"][0]["price"] is None


def test_invalid_arrival_is_rejected():
    raw = train()
    raw["arrivalTime"] = "12:00"
    assert snapshot([raw])["direct"] == []


def test_robots_rules():
    rules = "User-agent: *\nDisallow: /*?*\nDisallow: /private/*\nAllow: /"
    assert robots_allowed(rules, "https://example.org/public")
    assert not robots_allowed(rules, "https://example.org/public?date=1")
    assert not robots_allowed(rules, "https://example.org/private/x")
