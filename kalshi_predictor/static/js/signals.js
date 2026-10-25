// Signals tab: predict the busiest live markets at once and rank them by confidence.
import * as api from "./api.js";
import { handOff } from "./market.js";
import { go, setContracts, state } from "./state.js";
import { capitalize, cents, compact, dirBadge, el, icon, percent, store } from "./util.js";

export const tab = "signals";
export const sidebar = false;

const STRONG = 0.6; // confidence at which a call of a move is strong

function isStrong(r) {
  return (r.signal === "up" || r.signal === "down") && r.confidence >= STRONG;
}
let last = null; // the latest scan, kept while switching tabs

export function mount(root) {
  const page = el("div", "page");
  root.append(page);
  const horizon = state.info.horizon_s;
  let alive = true;
  let autoTimer = null;
  let size = store.get("scanSize", 20);
  let record = store.get("scanRecord", true);
  let auto = store.get("scanAuto", false);

  const sizes = el("div", "segmented");
  for (const n of [10, 20, 30, 40]) {
    const b = el("button", "", String(n));
    b.type = "button";
    b.setAttribute("aria-pressed", String(n === size));
    b.addEventListener("click", () => {
      size = n;
      store.set("scanSize", n);
      for (const other of sizes.children) other.setAttribute("aria-pressed", String(other === b));
    });
    sizes.append(b);
  }
  const recordBox = checkbox(record, (on) => { record = on; store.set("scanRecord", on); });
  const autoBox = checkbox(auto, (on) => {
    auto = on;
    store.set("scanAuto", on);
    clearTimeout(autoTimer);
    if (on) autoTimer = setTimeout(scan, horizon * 1000);
  });
  const contracts = el("input", "field");
  Object.assign(contracts, { type: "number", min: 1, max: 100000, value: state.contracts });
  contracts.setAttribute("aria-label", "Paper trade size in contracts");
  contracts.addEventListener("change", () => {
    const n = Math.round(Number(contracts.value));
    if (n >= 1 && n <= 100000) setContracts(n);
    contracts.value = state.contracts;
  });
  const button = el("button", "btn primary", icon("bolt"), "Scan now");
  button.addEventListener("click", scan);

  const summary = el("div", "scan-summary");
  const results = el("div", "");
  page.append(
    el("div", "page-head", el("div", "",
      el("h1", "", "Signals"),
      el("p", "lead", "Predicts the markets trading most on Kalshi right now, all at once, and ranks them by how sure " +
        "the model is that the price moves in the next " + horizon + " seconds."))),
    el("div", "toolbar",
      el("div", "toolbar-group", el("span", "toolbar-label", "Markets"), sizes),
      el("label", "switch", recordBox, "Paper trade them"),
      el("label", "size-label", contracts, " contracts"),
      el("label", "switch", autoBox, "Scan every " + horizon + "s"),
      el("div", "toolbar-end", button)),
    summary,
    results,
  );

  async function scan() {
    clearTimeout(autoTimer);
    button.disabled = true;
    button.replaceChildren(el("span", "spinner"), "Scanning...");
    results.classList.add("loading");
    try {
      last = await api.post("/api/scan", { markets: size, contracts: state.contracts, record });
      if (alive) render();
    } catch (err) {
      if (alive) summary.replaceChildren(el("div", "notice error", err.message));
    } finally {
      button.disabled = false;
      button.replaceChildren(icon("bolt"), "Scan now");
      results.classList.remove("loading");
      if (alive && auto) autoTimer = setTimeout(scan, horizon * 1000);
    }
  }

  function render() {
    const rows = last.rows;
    const strong = rows.filter(isStrong).length;
    summary.replaceChildren(
      el("span", "", "Scanned the " + rows.length + " busiest markets over the last " + Math.round(last.span_s) +
        "s of trading, at " + new Date(last.scanned_at * 1000).toLocaleTimeString() + ". "),
      el("strong", "", strong + (strong === 1 ? " strong signal" : " strong signals")),
      el("span", "muted", " (at least " + percent(STRONG) + " sure of a move)."),
    );
    const table = el("table", "signals");
    table.append(el("thead", "", el("tr", "",
      el("th", "", "#"), el("th", "", "Call"), el("th", "", "Confidence"), el("th", "num", "Flat / lean"),
      el("th", "num", "Price"), el("th", "num", "Trades"), el("th", "", "Market"))));
    const body = el("tbody");
    rows.forEach((r, i) => body.append(row(r, i)));
    table.append(body);
    results.replaceChildren(el("div", "card table-card", table));
  }

  if (last) render();
  else scan();
  return () => {
    alive = false;
    clearTimeout(autoTimer);
  };
}

function checkbox(checked, onChange) {
  const box = el("input");
  box.type = "checkbox";
  box.checked = checked;
  box.addEventListener("change", () => onChange(box.checked));
  return box;
}

function row(r, i) {
  const p = r.prediction;
  const tr = el("tr", isStrong(r) ? "strong" : "");
  tr.tabIndex = 0;
  if (!p.probs) {
    tr.append(el("td", "muted num", i + 1), el("td", "muted", "–"), el("td", "muted", capitalize(p.problem)),
      el("td"), el("td"), el("td", "num", compact(r.trades)), marketCell(r));
  } else {
    const meter = el("div", "meter");
    const fill = el("div", "fill " + r.signal);
    fill.style.width = percent(r.confidence, 1);
    meter.append(fill);
    const lean = r.signal === "flat" ? (p.probs.up >= p.probs.down ? "up" : "down") : null;
    tr.append(
      el("td", "muted num", i + 1),
      el("td", "", dirBadge(r.signal)),
      el("td", "", el("div", "confidence", meter, el("span", "num", percent(r.confidence)))),
      el("td", "num muted", lean ? "leans " + lean : percent(p.probs.flat)),
      el("td", "num", cents(p.price)),
      el("td", "num", compact(r.trades)),
      marketCell(r),
    );
  }
  const open = () => {
    if (p.probs) handOff(Object.assign({ title: r.title, paper_trade: r.paper_trade }, p));
    go("/market/" + encodeURIComponent(r.ticker));
  };
  tr.addEventListener("click", open);
  tr.addEventListener("keydown", (event) => { if (event.key === "Enter") open(); });
  return tr;
}

function marketCell(r) {
  const name = r.name && !r.title.includes(r.name) ? r.name : "";
  return el("td", "market-cell",
    el("div", "market-cell-title", r.title),
    el("div", "market-cell-sub", r.category ? el("span", "tag", r.category) : null, name ? el("span", "muted", name) : null));
}
