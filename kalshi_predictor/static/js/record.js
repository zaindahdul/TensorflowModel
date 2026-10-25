// Track record tab: how the paper-traded predictions turned out.
import * as api from "./api.js";
import { lineChart, timeLabel } from "./chart.js";
import { outcomeBadge } from "./market.js";
import { go, state } from "./state.js";
import { capitalize, cents, dirBadge, el, icon, money, moneyClass, percent, store } from "./util.js";

export const tab = "record";
export const sidebar = false;

const PAGE_SIZE = 100;
const RANGES = { today: "Today", week: "7 days", all: "All time" };
const SOURCES = { all: "Everything", manual: "Opened by you", scan: "From scans" };

function since(range) {
  if (range === "today") {
    const midnight = new Date();
    midnight.setHours(0, 0, 0, 0);
    return midnight.getTime() / 1000;
  }
  return range === "week" ? Date.now() / 1000 - 7 * 86400 : undefined;
}

export function mount(root) {
  const page = el("div", "page");
  root.append(page);
  let alive = true;
  let timer = null;
  let range = store.get("recordRange", "all");
  let source = store.get("recordSource", "all");
  let shown = PAGE_SIZE;

  const tiles = el("div", "stat-tiles");
  const equityBox = el("div", "chart-box");
  let calibrationBox;
  const charts = el("div", "grid record-charts", el("section", "card",
    el("div", "card-head", el("div", "", el("h2", "", "Paper profit over time"),
      el("div", "card-sub", "Running total after each trade closed, fees included"))),
    equityBox),
    el("section", "card",
      el("div", "card-head", el("div", "", el("h2", "", "Is the model as sure as it says?"),
        el("div", "card-sub", "How often calls were right, by how confident the model was"))),
      calibrationBox = el("div", "calibration")));
  const table = el("div", "");
  let equity = null;
  const exportLink = el("a", "btn small", icon("download"), "Export CSV");
  exportLink.title = "Download every prediction in these filters";
  const filters = el("div", "toolbar",
    segmented(RANGES, () => range, (v) => { range = v; store.set("recordRange", v); load(); }),
    segmented(SOURCES, () => source, (v) => { source = v; store.set("recordSource", v); load(); }),
    el("div", "toolbar-end", exportLink));
  page.append(
    el("div", "page-head", el("div", "",
      el("h1", "", "Track record"),
      el("p", "lead", "Every prediction is paper traded with no real money: Up buys Yes and Down buys No at the ask, " +
        "then sells at the bid " + state.info.horizon_s + " seconds later, after Kalshi's fees. Flat calls don't trade. " +
        "A call is right when the price really moved that way within " + state.info.horizon_s + " seconds of the last trade the model saw."))),
    filters, tiles, charts, table,
  );

  async function load() {
    clearTimeout(timer);
    page.classList.add("refreshing");
    try {
      const data = await api.get("/api/history", { since: since(range), source: source === "all" ? undefined : source, limit: shown });
      if (!alive) return;
      for (const group of filters.querySelectorAll(".segmented")) group.refresh();
      const query = new URLSearchParams(Object.entries({ since: since(range), source: source === "all" ? "" : source })
        .filter(([, v]) => v));
      exportLink.href = "/api/history.csv" + (query.size ? "?" + query : "");
      renderTiles(tiles, data.summary);
      renderCalibration(calibrationBox, data.calibration);
      const points = equityPoints(data.equity);
      if (!equity) {
        equity = lineChart(equityBox, points, {
          label: "Paper profit over time", height: 200, step: true, zero: true, minSpan: 1,
          formatValue: (v) => money(v), formatTime: timeLabel,
          emptyText: "No closed paper trades yet",
          tooltipRows: (p) => (p.name ? [["Trade", p.name], ["This trade", money(p.pnl)]] : []),
        });
      } else {
        equity(points);
      }
      renderTable(table, data, () => { shown += PAGE_SIZE; load(); });
      timer = setTimeout(load, data.summary.pending ? 3000 : 15000);
    } catch (err) {
      if (alive) table.replaceChildren(el("div", "notice error", err.message));
      timer = setTimeout(load, 15000);
    } finally {
      page.classList.remove("refreshing");
    }
  }

  load();
  return () => {
    alive = false;
    clearTimeout(timer);
    equity?.destroy();
  };
}

// The curve starts from zero just before the first trade closed.
function equityPoints(equity) {
  if (!equity.length) return [];
  return [{ t: equity[0].t - 60, v: 0 }, ...equity.map((p) => ({ ...p, v: p.total }))];
}

function segmented(choices, current, onPick) {
  const box = el("div", "segmented");
  for (const [value, label] of Object.entries(choices)) {
    const b = el("button", "", label);
    b.type = "button";
    b.addEventListener("click", () => onPick(value));
    b.dataset.value = value;
    box.append(b);
  }
  box.refresh = () => {
    for (const b of box.children) b.setAttribute("aria-pressed", String(b.dataset.value === current()));
  };
  box.refresh();
  return box;
}

// One row per confidence band: a bar for how often calls were right, with a
// tick where it would be if the model were exactly as sure as it said.
function renderCalibration(box, bands) {
  const rows = bands.filter((b) => b.calls).map((b) => {
    const label = b.low === 0 ? "under " + percent(b.high) : b.high >= 1 ? percent(b.low) + "+" : percent(b.low) + "–" + percent(b.high);
    const rate = b.right / b.calls;
    const fill = el("div", "cal-fill");
    fill.style.width = percent(rate, 1);
    const tick = el("div", "cal-tick");
    tick.style.left = percent(b.mean_confidence, 1);
    return el("div", "cal-row",
      el("span", "cal-label", label + " sure"),
      el("div", "cal-track", fill, tick),
      el("span", "cal-value num", el("strong", "", percent(rate)), el("span", "muted", " of " + b.calls)));
  });
  if (!rows.length) {
    box.replaceChildren(el("div", "empty", "Nothing checked yet."));
    return;
  }
  box.replaceChildren(
    ...rows,
    el("div", "cal-key", el("span", "key-bar"), "share of calls that were right", el("span", "key-tick"),
      "how sure the model was, on average"),
  );
}

function tile(label, value, valueClass, sub) {
  return el("div", "stat", el("div", "stat-label", label), el("div", "stat-value " + (valueClass || ""), value), el("div", "stat-sub", sub));
}

function renderTiles(box, s) {
  const dash = "–";
  box.replaceChildren(
    tile("Paper profit", s.trades ? money(s.pnl) : dash, moneyClass(s.pnl),
      s.trades + (s.trades === 1 ? " trade, " : " trades, ") + s.winning_trades + " won, $" + s.fees.toFixed(2) + " in fees"),
    tile("Calls of a move that were right", s.move_calls ? percent(s.move_correct / s.move_calls) : dash, "",
      s.move_correct + " of " + s.move_calls + " Up or Down calls"),
    tile("All calls right", s.checked ? percent(s.correct / s.checked) : dash, "",
      s.correct + " of " + s.checked + " checked, versus " + percent(state.info.accuracy || 0) + " on the test set"),
    tile("Predictions", String(s.predictions), "",
      s.pending ? s.pending + " being checked now" : "all checked"),
  );
}

function tradeText(t) {
  if (t.skipped) return el("span", "muted", "No trade");
  const bought = t.contracts.toLocaleString() + " " + capitalize(t.side) + " @ " + cents(t.entry_price);
  if (t.problem) return el("span", "muted", bought + ", not sold");
  if (t.pnl === undefined) return el("span", "muted", bought + ", selling soon");
  return el("span", "", bought + " → " + cents(t.exit_price));
}

function renderTable(box, data, more) {
  if (!data.entries.length) {
    box.replaceChildren(el("div", "card empty", "No predictions here yet. Open a market or run a scan and they will show up."));
    return;
  }
  const body = el("tbody");
  for (const e of data.entries) {
    const tr = el("tr", "");
    const name = e.title && e.name && !e.title.includes(e.name) ? e.title + " · " + e.name : e.title || e.name || e.ticker;
    let actual;
    if (e.outcome) actual = el("span", "call-result", dirBadge(e.outcome.actual), outcomeBadge(e.outcome.correct));
    else if (e.error) actual = el("span", "muted", "could not check");
    else actual = el("span", "muted", el("span", "spinner"), " checking");
    const pnl = e.trade.pnl;
    tr.append(
      el("td", "muted num nowrap", new Date(e.created * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })),
      el("td", "market-cell", el("div", "market-cell-title", name), el("div", "market-cell-sub",
        e.source === "scan" ? el("span", "tag", "scan") : null, el("span", "ticker", e.ticker))),
      el("td", "nowrap", dirBadge(e.predicted), " ", el("span", "muted num", percent(e.probs[e.predicted]))),
      el("td", "nowrap", actual),
      el("td", "nowrap", tradeText(e.trade)),
      el("td", "num nowrap " + (pnl === undefined ? "muted" : moneyClass(pnl)), pnl === undefined ? "" : money(pnl)),
    );
    tr.addEventListener("click", () => go("/market/" + encodeURIComponent(e.ticker)));
    body.append(tr);
  }
  const table = el("table", "history",
    el("thead", "", el("tr", "", el("th", "", "When"), el("th", "", "Market"), el("th", "", "Call"),
      el("th", "", "Actual"), el("th", "", "Paper trade"), el("th", "num", "P&L"))),
    body);
  const card = el("div", "card table-card", table);
  const parts = [card];
  if (data.entries.length < data.summary.predictions) {
    const button = el("button", "btn", "Show more");
    button.addEventListener("click", more);
    parts.push(el("div", "more", el("span", "muted", "Showing " + data.entries.length + " of " + data.summary.predictions), button));
  }
  box.replaceChildren(...parts);
}
