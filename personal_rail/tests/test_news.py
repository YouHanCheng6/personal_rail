from datetime import date

from personal_rail.news import FEEDS, parse_announcements


def test_news_only_dated_recent_official_links_and_no_ai_summary():
    html = """<ul>
    <li><a href="../../zxdt_news/202609/t20260922_1.html">广州局加开公告</a><span class="zxdt_time_in">(2026-09-22)</span></li>
    <li><a href="https://evil.example/news">假消息</a><span class="zxdt_time_in">(2026-09-23)</span></li>
    <li><a href="../../zxdt/202205/old.html">旧规则</a><span class="zxdt_time_in">(2022-05-31)</span></li>
    <li><a href="../../zxdt/future.html">未来日期</a><span class="zxdt_time_in">(2026-09-24)</span></li></ul>"""
    items = parse_announcements(html, FEEDS[3][1], "广州局", date(2026, 9, 23))
    assert len(items) == 1
    assert items[0]["published"] == "2026-09-22"
    assert items[0]["title"] == "广州局加开公告"


def test_category_filters_official_domains_dates_and_travel_topics():
    from personal_rail.news import parse_category

    html = """<ul><li><a href="http://www.caac.gov.cn/XWZX/MHYW/a.html">假期运输保障</a><span class="n_date">2026-09-22</span></li><li><a href="https://evil.example/x">机场通知</a><span class="n_date">2026-09-22</span></li><li><a href="/old">机场旧通知</a><span class="n_date">2020-01-01</span></li></ul>"""
    items = parse_category(
        html,
        "https://www.caac.gov.cn/XWZX/MHYW/",
        "民航局",
        date(2026, 9, 23),
        "flight",
    )
    assert len(items) == 1 and items[0]["category"] == "flight"
    assert items[0]["url"].startswith("https://www.caac.gov.cn/")


def test_road_category_does_not_classify_high_speed_rail_as_road():
    from personal_rail.news import parse_category

    html = '<ul><li><a href="/a">高速铁路开通</a><span class="date">2026-09-22</span></li><li><a href="/b">假期公路出行提示</a><span class="date">2026-09-22</span></li></ul>'
    items = parse_category(
        html, "https://www.mot.gov.cn/", "交通运输部", date(2026, 9, 23), "coach"
    )
    assert len(items) == 1 and items[0]["title"] == "假期公路出行提示"
