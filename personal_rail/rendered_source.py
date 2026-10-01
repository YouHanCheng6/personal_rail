"""Bounded subprocess transport and validation of visible flight cards."""

import asyncio
import json
import os
import re
import sys
import signal
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from .service import ROOT


async def read_rendered(url):
    # A dedicated optional runtime keeps DeerFlow's Python environment unchanged.
    local = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    executable = str(local) if local.exists() else sys.executable
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        in {
            "PATH",
            "HOME",
            "TMPDIR",
            "TEMP",
            "SystemRoot",
            "LANG",
            "SSL_CERT_FILE",
            "PLAYWRIGHT_BROWSERS_PATH",
        }
    }
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".local" / "browsers")
    process = await asyncio.create_subprocess_exec(
        executable,
        str(ROOT / "rendered.py"),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=env,
        start_new_session=os.name != "nt",
    )
    try:
        output, _ = await asyncio.wait_for(
            process.communicate(json.dumps({"url": url}).encode()), timeout=95
        )
        if process.returncode or len(output) > 2_000_000:
            raise ValueError("动态公开页面未能读取")
        value = json.loads(output)
        if "error" in value:
            raise ValueError(value["error"])
        return value
    finally:
        if process.returncode is None:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.kill()
            await process.wait()


def parse_rendered(data, initial, origin, destination, wanted, url, observed):
    if (data.get("origin"), data.get("destination"), data.get("date")) != (
        origin,
        destination,
        str(wanted),
    ):
        raise ValueError("动态页面查询条件不符")
    rows = data.get("cards", [])
    counts = [
        int(x)
        for text in data.get("count_text", [])
        for x in re.findall(r"(\d+)个航班", text)
    ]
    if not counts or max(counts) != len(rows) or not rows:
        raise ValueError("航班数量未能核对")
    airports = {
        side: sorted({r[side] for r in initial}) for side in ("origin", "destination")
    }

    def airport(text, side):
        short = re.sub(r"T\d+.*$", "", text).replace("国际", "").strip()
        matches = [name for name in airports[side] if short in name.replace("国际", "")]
        return matches[0] if len(matches) == 1 else None

    offers = []
    for raw in rows:
        card = BeautifulSoup(raw, "html.parser")

        def text(selector):
            el = card.select_one(selector)
            return el.get_text(" ", strip=True) if el else ""

        code = re.search(r"([A-Z0-9]{2}\d{3,4})$", text(".flight-item-name"))
        duration = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?", text(".f-line-to i"))
        money = re.fullmatch(r"[¥￥]([\d,.]+)", text(".head-prices strong em"))
        a, b = (
            airport(text(".f-startTime em"), "origin"),
            airport(text(".f-endTime em"), "destination"),
        )
        if not code or not duration or not a or not b:
            continue
        departure = datetime.fromisoformat(
            str(wanted) + "T" + text(".f-startTime strong")
        )
        minutes = int(duration[1] or 0) * 60 + int(duration[2] or 0)
        arrival = departure + timedelta(minutes=minutes)
        if not 0 < minutes <= 1440 or arrival.strftime("%H:%M") != text(
            ".f-endTime strong"
        ):
            continue
        price = float(money[1].replace(",", "")) if money else None
        if price is not None and not 0 < price <= 100000:
            price = None
        cabin = text(".head-prices > i")
        offers.append(
            {
                "mode": "flight",
                "origin": a,
                "destination": b,
                "origin_city": origin,
                "destination_city": destination,
                "departure": departure.isoformat(),
                "arrival": arrival.isoformat(),
                "label": code[1],
                "price": price,
                "price_complete": False,
                "source_url": url,
                "source_label": "同程公开航班页（动态列表）",
                "observed_at": observed,
                "source_date": str(wanted),
                "coverage": "page_complete",
                "missing": (["票价未公布"] if price is None else [])
                + ["含税及服务费总价未核实"],
                "note": f"公开页面加载后报价；{cabin}。已核对页面列出航班数，不代表全网覆盖；税费、行李与库存未核实。",
            }
        )
    if not offers:
        raise ValueError("动态班次缺少可核实机场或字段")
    if len(offers) != len(rows):
        for offer in offers:
            offer["coverage"] = "partial"
            offer["note"] += (
                f"页面{len(rows)}个航班中仅{len(offers)}个通过字段核验，其余未采用。"
            )
    return offers
