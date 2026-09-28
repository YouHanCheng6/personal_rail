from datetime import date
from personal_rail.news import parse_announcements, FEEDS


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
