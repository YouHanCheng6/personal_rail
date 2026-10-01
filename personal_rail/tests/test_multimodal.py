from datetime import date
from pathlib import Path
import pytest
from personal_rail.multimodal import (
    parse_flights,
    parse_coaches,
    read_nuxt,
    cache_usable,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_flight_date_and_visible_price():
    html = (FIXTURES / "flight-public.html").read_text()
    rows = parse_flights(
        html,
        "北京",
        "南昌",
        date(2026, 9, 30),
        "https://www.ly.com/",
        "2026-09-29T22:00:00",
    )
    assert len(rows) == 10 and all(not r["price_complete"] for r in rows)
    with pytest.raises(ValueError):
        parse_flights(
            html,
            "北京",
            "南昌",
            date(2026, 10, 6),
            "https://www.ly.com/",
            "2026-09-29T22:00:00",
        )


def test_coach_missing_fields_remain_visible():
    rows = parse_coaches(
        (FIXTURES / "coach-public.html").read_text(),
        "揭阳",
        "龙川",
        date(2026, 9, 30),
        "https://bus.ctrip.com/",
        "2026-09-29T22:00:00",
    )
    assert rows and any(r["missing"] for r in rows)


def test_page_javascript_is_not_executed():
    with pytest.raises(ValueError):
        read_nuxt("window.__NUXT__=process.exit()")


def test_stale_cache_is_not_new_price():
    assert cache_usable(1000, 1500)
    assert not cache_usable(1000, 1700)


def coach_page(state):
    import json

    return (
        '<script id="__NEXT_DATA__">'
        + json.dumps({"props": {"pageProps": {"initialState": state}}})
        + "</script>"
    )


def test_chinese_coach_route_empty_is_not_parse_failure():
    html = coach_page(
        {"fromPy": "北京", "toPy": "龙川县", "lines": [], "noContent": True}
    )
    assert (
        parse_coaches(
            html,
            "北京",
            "龙川",
            date(2026, 10, 7),
            "https://bus.ctrip.com/schedule/beijing-longchuan",
            "2026-10-01",
        )
        == []
    )
    with pytest.raises(ValueError):
        parse_coaches(
            html,
            "揭阳",
            "龙川",
            date(2026, 10, 7),
            "https://bus.ctrip.com/",
            "2026-10-01",
        )


def test_missing_coach_lines_is_not_an_empty_route():
    with pytest.raises(ValueError):
        parse_coaches(
            coach_page({"fromPy": "北京", "toPy": "龙川县"}),
            "北京",
            "龙川",
            date(2026, 10, 7),
            "https://bus.ctrip.com/",
            "2026-10-01",
        )
