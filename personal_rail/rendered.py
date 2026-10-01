"""Isolated normal-browser reader for public, dynamically rendered flight cards.

No account/profile reuse, private API calls, stealth flags or booking interactions.
Invoked by the source collector only after robots and rate-limit checks.
"""

import asyncio
import json
import sys
from urllib.parse import urlsplit


async def read(url):
    from playwright.async_api import async_playwright

    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.ly.com"
        or not parsed.path.startswith("/flights/itinerary/oneway/")
    ):
        raise ValueError("Unsupported public page")
    async with async_playwright() as engine:
        browser = await engine.chromium.launch()
        initial_html = None
        try:
            page = await browser.new_page(locale="zh-CN", timezone_id="Asia/Shanghai")
            response = await page.goto(
                url, wait_until="domcontentloaded", timeout=45000
            )
            if (
                response is None
                or response.status >= 400
                or urlsplit(page.url).netloc != parsed.netloc
            ):
                raise ValueError("Public page unavailable")
            initial_html = await response.text()
            await page.locator(".flight-item-head").first.wait_for(timeout=20000)
            await page.get_by_text("加载剩余航班数据", exact=True).wait_for(
                state="hidden", timeout=40000
            )
            # Require an unchanged visible list after hydration, not the first SSR ten.
            previous, stable = None, 0
            for _ in range(20):
                html = await page.locator(".flight-item-head").evaluate_all(
                    "els => els.map(e => e.outerHTML)"
                )
                if html == previous:
                    stable += 1
                else:
                    previous, stable = html, 0
                if stable >= 3:
                    break
                await asyncio.sleep(1)
            if stable < 3:
                raise ValueError("Flight list did not settle")
            return {
                "initial_html": initial_html,
                "origin": await page.locator(".fl-input-dep").input_value(),
                "destination": await page.locator(".fl-input-arr").input_value(),
                "date": await page.get_by_placeholder(
                    "选择出发日期", exact=True
                ).input_value(),
                "cards": html,
                "count_text": await page.locator("b")
                .filter(has_text="个航班")
                .all_text_contents(),
            }
        except Exception:
            if initial_html:
                return {
                    "initial_html": initial_html,
                    "render_error": "动态列表未能完成加载，只有初始部分航班。",
                }
            raise
        finally:
            await browser.close()


if __name__ == "__main__":
    try:
        request = json.loads(sys.stdin.read(4096))
        print(json.dumps(asyncio.run(read(request["url"])), ensure_ascii=False))
    except Exception:
        # Never return browser internals, inherited environment or challenge material.
        print(
            json.dumps(
                {"error": "动态公开页面未能完整读取；未绕过验证。"}, ensure_ascii=False
            )
        )
        sys.exit(1)
