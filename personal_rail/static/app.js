"use strict";
const $ = (s) => document.querySelector(s);
const labels = { rail: "火车", flight: "飞机", coach: "汽车" };
const esc = (x) =>
  String(x ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const link = (u) => {
  try {
    const x = new URL(u);
    return x.protocol === "https:" && !x.username ? esc(x.href) : "";
  } catch {
    return "";
  }
};
const stamp = (x) => (x ? String(x).replace("T", " ").slice(0, 16) : "未公布");
const money = (x) => (x == null ? "未公布" : `¥${Number(x)}`);
let current = null,
  active = "rail",
  page = 0,
  controller = null,
  sequence = 0;
const selectedPlaces = {};
function price(r) {
  if (r.legs) {
    const values = r.legs.map(price);
    return values.every(Number.isFinite)
      ? values.reduce((a, b) => a + b, 0)
      : Infinity;
  }
  return r.seats?.length
    ? Math.min(...r.seats.filter((s) => s.price != null).map((s) => s.price))
    : (r.price ?? Infinity);
}
function visibleRows() {
  const term = $("#filter").value.trim().toLowerCase();
  const rows = (current?.items[active] || []).filter((r) =>
    [
      r.label,
      r.origin,
      r.destination,
      ...(r.seats || []).map((s) => s.name),
      ...(r.legs || []).flatMap((l) => [
        l.origin,
        l.destination,
        ...(l.seats || []).map((s) => s.name),
      ]),
    ]
      .join(" ")
      .toLowerCase()
      .includes(term),
  );
  const sort = $("#sort").value;
  return rows.sort((a, b) =>
    sort === "price"
      ? price(a) - price(b)
      : String(a[sort] || "~").localeCompare(String(b[sort] || "~"), "zh-CN"),
  );
}
function source(r) {
  const u = link(r.source_url || r.url);
  return u
    ? `<a href="${u}" target="_blank" rel="noopener noreferrer">${esc(r.source_label || r.provider || "查看来源")} ↗</a>`
    : "未提供来源链接";
}
function render() {
  if (!current) return;
  document.querySelectorAll("[data-mode]").forEach((b) => {
    const m = b.dataset.mode;
    b.textContent = `${labels[m]} · ${(current.items[m] || []).length}`;
    b.setAttribute("aria-pressed", String(m === active));
  });
  const waiting = current.cancelled
    ? []
    : current.pending.filter((j) => j[0] === active);
  let scope =
    active === "rail"
      ? "展示平台提供的直达和铁路换乘；不自行拼接车次，不混合飞机或汽车。"
      : active === "coach"
        ? "仅查输入两地的汽车公开时刻资料。此来源未提供指定日期查询；其他日期会单独标注。"
        : "航班按真实起降机场展示，不自动连接火车或汽车。";
  if (active === "flight" && current.airports.length)
    scope += `<div class="airport-list">${current.airports.map((a) => `<p>${esc(a.airport)}${a.distance_km != null ? ` · 约${a.distance_km}公里` : ""}<br><span class="note">${esc(a.distance_basis)}</span></p>`).join("")}</div>`;
  $("#scope").innerHTML =
    `<p>${esc(current.query.origin)} → ${esc(current.query.destination)} · ${esc(current.query.departure_date)}${current.cancelled ? " · 查询已停止" : ""}</p>` +
    scope;
  const rows = visibleRows(),
    pages = Math.ceil(rows.length / 25);
  page = Math.min(page, Math.max(0, pages - 1));
  const railRows = current.items.rail || [];
  const breakdown =
    active === "rail"
      ? `直达 ${railRows.filter((r) => r.kind !== "transfer").length} 条 · 平台换乘 ${railRows.filter((r) => r.kind === "transfer").length} 条 · `
      : "";
  $("#count").textContent =
    breakdown +
    `已取得 ${(current.items[active] || []).length} 条资料 · 当前筛选 ${rows.length} 条${waiting.length ? ` · ${waiting.length} 个方向仍在查询` : ""}；价格为来源公布值，未核验可售状态。`;
  $("#rows").innerHTML = rows.length
    ? rows
        .slice(page * 25, (page + 1) * 25)
        .map(
          (r) =>
            `<article class="record"><div class="record-top"><h3>${esc(r.origin)} → ${esc(r.destination)} · ${esc(r.label)}</h3>${r.kind === "transfer" ? `<span class="note">平台换乘 · ${r.legs.length - 1} 次</span>` : ""}${r.mode !== "rail" ? `<span class="fare">${money(r.price)}${r.price != null && !r.price_complete ? "起" : ""}</span>` : ""}</div><p>${stamp(r.departure)} — ${stamp(r.arrival)}</p>${r.legs ? transferDetails(r) : ""}${r.seats ? `<p class="seats">${r.seats.map((s) => `<span>${esc(s.name)} ${money(s.price)}</span>`).join("")}</p>` : ""}${r.source_date && r.source_date !== current.query.departure_date ? `<p class="warning">来源日期 ${esc(r.source_date)}，不是所选日期的已确认班次。</p>` : ""}${(r.missing || []).length ? `<p class="warning">${r.missing.map(esc).join("；")}</p>` : ""}<p class="note">${esc(r.note || "公开时刻与参考票价；余票、实际车型及上座率未核实。")}</p><p class="note">${(r.sources || [r]).map((s) => `${source(s)} · 读取 ${stamp(s.observed_at)}`).join("<br>")}</p></article>`,
        )
        .join("")
    : `<p class="empty">${waiting.length ? "正在读取此类交通资料…" : $("#filter").value ? "没有匹配筛选条件的资料。" : "本次未取得此类班次资料；不代表没有运营班次。"}</p>`;
  $("#page").textContent = `${pages ? page + 1 : 0} / ${pages}`;
  $("#prev").disabled = page === 0;
  $("#next").disabled = page + 1 >= pages;
  $("#sources").innerHTML = current.sources
    .map(
      (s) =>
        `<div class="source-row"><strong>${labels[s.mode] || ""} · ${esc(s.direction || "")}</strong><p>${esc(s.message || { cached: "读取缓存，保留原始读取时间", fetched: "已读取所选日期资料" }[s.status] || s.status)}</p>${source(s)}</div>`,
    )
    .join("");
  const messages = current.sources.filter(
    (s) =>
      s.mode === active &&
      (s.mode === "coach" ||
        s.status === "unavailable" ||
        s.status === "reference_empty" ||
        s.status === "unsupported" ||
        s.status === "blocked" ||
        s.status === "reference"),
  );
  $("#scope").innerHTML += messages
    .map(
      (s) =>
        `<p class="warning">${esc(s.direction || "")}：${esc(s.message)}</p>`,
    )
    .join("");
  $("#export").disabled = false;
}
function transferDetails(r) {
  return `<ol class="rail-legs">${r.legs
    .map((leg, i) => {
      const next = r.legs[i + 1];
      const wait = next
        ? Math.round(
            (Date.parse(next.departure) - Date.parse(leg.arrival)) / 60000,
          )
        : null;
      return `<li><strong>${esc(leg.origin)} → ${esc(leg.destination)} · ${esc(leg.train)}</strong><p>${stamp(leg.departure)} — ${stamp(leg.arrival)}</p><p class="seats">${(leg.seats || []).map((s) => `<span>${esc(s.name)} ${money(s.price)}</span>`).join("")}</p>${next ? `<p class="note">${esc(leg.destination)} → ${esc(next.origin)} · 两车间隔 ${Number.isFinite(wait) && wait >= 0 ? wait + " 分钟" : "待核实"}${leg.destination !== next.origin ? " · 需跨站，接驳未核实" : " · 同站换乘"}</p>` : ""}</li>`;
    })
    .join("")}</ol>`;
}
async function consume(response, run) {
  if (!response.ok) {
    const d = await response.json();
    throw Error(
      typeof d.detail === "string" ? d.detail : "输入不符合查询要求。",
    );
  }
  const reader = response.body.getReader(),
    decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let end;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const event = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const line = event.split("\n").find((l) => l.startsWith("data: "));
      if (!line || run !== sequence) continue;
      const data = JSON.parse(line.slice(6));
      if (event.startsWith("event: error")) throw Error(data.message);
      current = data;
      render();
      $("#status").textContent = data.complete
        ? "查询结束。展示已取得的全部资料；未取得的方向见来源状态。"
        : `正在查询 · 剩余 ${data.pending.length} 个方向：${data.pending.map((j) => `${labels[j[0]]} ${j[1]}→${j[2]}`).join("、")}`;
    }
  }
}
$("#search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  controller?.abort();
  controller = new AbortController();
  const run = ++sequence;
  current = null;
  page = 0;
  const modes = [...document.querySelectorAll("[name=mode]:checked")].map(
    (x) => x.value,
  );
  if (!modes.length) {
    $("#status").textContent = "请至少选择一种交通。";
    return;
  }
  active = modes[0];
  $("#rows").innerHTML = '<p class="empty">正在确定查询范围…</p>';
  $("#sources").innerHTML = "";
  $("#scope").innerHTML = "";
  $("#count").textContent = "";
  $("#export").disabled = true;
  $("#submit").disabled = true;
  $("#cancel").hidden = false;
  $("#status").textContent = "正在查询，不调用模型。";
  try {
    await consume(
      await fetch("/api/search?stream=true", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          origin: $("#origin").value.trim(),
          destination: $("#destination").value.trim(),
          origin_id: selectedPlaces.origin?.id || null,
          destination_id: selectedPlaces.destination?.id || null,
          departure_date: $("#date").value,
          airport_radius_km: Number($("#radius").value),
          modes,
        }),
      }),
      run,
    );
  } catch (e) {
    if (run === sequence) {
      if (current) {
        current.cancelled = true;
        render();
      }
      $("#status").textContent =
        e.name === "AbortError"
          ? "已取消；已取得的资料保留，查询未完成。"
          : e.message;
    }
  } finally {
    if (run === sequence) {
      $("#submit").disabled = false;
      $("#cancel").hidden = true;
    }
  }
});
$("#cancel").onclick = () => controller?.abort();
for (const side of ["origin", "destination"]) {
  $(`#${side}`).addEventListener("input", () => {
    delete selectedPlaces[side];
    $(`#${side}-options`).innerHTML = "";
  });
  document.querySelector(`[data-place="${side}"]`).onclick = async () => {
    const name = $(`#${side}`).value.trim(),
      box = $(`#${side}-options`);
    box.textContent = "查找中…";
    try {
      const d = await (
        await fetch("/api/places?q=" + encodeURIComponent(name))
      ).json();
      if (name !== $(`#${side}`).value.trim()) return;
      box.innerHTML = "";
      for (const p of d.items || []) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "place-option";
        b.textContent = p.name + " · " + p.address;
        b.onclick = () => {
          selectedPlaces[side] = p;
          $(`#${side}`).value = p.name;
          box.textContent = p.address;
        };
        box.append(b);
      }
      if (!d.items?.length)
        box.textContent = d.message || "未找到地点，请输入城市或车站。";
    } catch {
      box.textContent = "地点服务不可用。";
    }
  };
}
document.querySelectorAll("[data-mode]").forEach(
  (b) =>
    (b.onclick = () => {
      active = b.dataset.mode;
      page = 0;
      render();
    }),
);
$("#filter").oninput = () => {
  page = 0;
  render();
};
$("#sort").onchange = () => {
  page = 0;
  render();
};
$("#prev").onclick = () => {
  page--;
  render();
};
$("#next").onclick = () => {
  page++;
  render();
};
$("#export").onclick = () => {
  const url = URL.createObjectURL(
      new Blob([JSON.stringify(current, null, 2)], {
        type: "application/json",
      }),
    ),
    a = document.createElement("a");
  a.href = url;
  a.download = "沿线-查询资料.json";
  a.click();
  URL.revokeObjectURL(url);
};
$("#news-load").onclick = async () => {
  const box = $("#news");
  box.textContent = "读取中…";
  try {
    const d = await (
      await fetch("/api/news?category=" + $("#news-category").value)
    ).json();
    box.innerHTML =
      (d.items || [])
        .map(
          (r) =>
            `<p>${source({ ...r, source_label: r.title })} · ${esc(r.published || "日期未标注")}</p>`,
        )
        .join("") || "本次未取得公告。";
  } catch {
    box.textContent = "官方栏目暂不可用。";
  }
};
fetch("/api/meta")
  .then((r) => r.json())
  .then((d) => {
    $("#date").value = d.default_date;
    for (const r of d.routes) {
      const b = document.createElement("button");
      b.textContent = r.origin + " → " + r.destination;
      b.onclick = () => {
        $("#origin").value = r.origin;
        $("#destination").value = r.destination;
        delete selectedPlaces.origin;
        delete selectedPlaces.destination;
      };
      $("#quick").append(b);
    }
  })
  .catch(
    () => ($("#status").textContent = "服务未连接，请检查本地程序是否运行。"),
  );
