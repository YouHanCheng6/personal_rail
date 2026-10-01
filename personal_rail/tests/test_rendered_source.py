from datetime import date
import pytest
from personal_rail.rendered_source import parse_rendered

CARD = """<div class="flight-item-head"><p class="flight-item-name">江西航空RY8868</p><div class="f-startTime"><strong>20:00</strong><em>大兴机场</em></div><div class="f-line-to"><i>2h20m</i></div><div class="f-endTime"><strong>22:20</strong><em>昌北机场T2</em></div><div class="head-prices"><strong><em>¥960</em></strong><i>经济舱</i></div></div>"""


def data(card=CARD):
    return {
        "origin": "北京",
        "destination": "南昌",
        "date": "2026-10-01",
        "cards": [card],
        "count_text": ["（1个航班）"],
    }


def parse(value):
    return parse_rendered(
        value,
        [{"origin": "北京大兴国际机场", "destination": "南昌昌北国际机场"}],
        "北京",
        "南昌",
        date(2026, 10, 1),
        "https://www.ly.com/",
        "2026-09-30T10:00:00",
    )


def test_rendered_late_flight_and_cross_day_validation():
    row = parse(data())[0]
    assert row["price"] == 960 and row["label"] == "RY8868"
    assert not row["price_complete"]
    late = (
        CARD.replace("20:00", "22:40")
        .replace("22:20", "00:50")
        .replace("2h20m", "2h10m")
    )
    assert parse(data(late))[0]["arrival"] == "2026-10-02T00:50:00"


def test_rendered_mismatch_does_not_fabricate_complete_list():
    for change in [
        {"date": "2026-10-02"},
        {"count_text": ["（16个航班）"]},
        {"origin": "上海"},
    ]:
        with pytest.raises(ValueError):
            parse({**data(), **change})
    with pytest.raises(ValueError):
        parse(data(CARD.replace("大兴机场", "未知机场")))


def test_valid_dynamic_prices_survive_an_invalid_other_card():
    value = data()
    value["cards"].append(CARD.replace("大兴机场", "未知机场"))
    value["count_text"] = ["（2个航班）"]
    rows = parse(value)
    assert len(rows) == 1 and rows[0]["price"] == 960
    assert rows[0]["coverage"] == "partial"


def test_missing_flight_price_does_not_hide_the_flight():
    row = parse(data(CARD.replace("¥960", "暂无报价")))[0]
    assert row["price"] is None and "票价未公布" in row["missing"]
