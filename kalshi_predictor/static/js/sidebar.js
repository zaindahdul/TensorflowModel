// Sidebar: the markets trading most on Kalshi right now, refreshed every few seconds.
import * as api from "./api.js";
import { go } from "./state.js";
import { cents, compact, el, icon, store } from "./util.js";

const REFRESH_MS = 10000;

let root, list, sub, chipBox;
let markets = [];
let category = store.get("liveCategory", "All");
let timer = null;

export function setup(container) {
  root = container;
  sub = el("div", "card-sub", "Loading...");
  chipBox = el("div", "chips side-chips");
  list = el("div", "live-list");
  const refresh = el("button", "icon-btn", icon("refresh"));
  refresh.type = "button";
  refresh.setAttribute("aria-label", "Refresh live markets");
  refresh.addEventListener("click", load);
  root.append(
    el("div", "side-head", el("div", "", el("h2", "", el("span", "live-dot"), "Live now"), sub), refresh),
    chipBox,
    el("div", "flow-legend", el("span", "key yes"), "Yes buyers", el("span", "key no"), "No buyers",
      el("span", "muted", "· bar = activity")),
    list,
  );
  window.addEventListener("hashchange", markActive);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
  load();
}

function visible() {
  return !document.hidden && !document.querySelector(".layout").classList.contains("no-sidebar");
}

async function load() {
  clearTimeout(timer);
  timer = setTimeout(load, REFRESH_MS);
  if (!visible() && markets.length) return;
  root.classList.add("loading");
  try {
    const body = await api.get("/api/live", { limit: 30 });
    markets = body.markets;
    sub.textContent = "Most traded in the last " + Math.max(1, Math.round(body.span_s)) + "s of trading";
    render();
  } catch (err) {
    if (!markets.length) list.replaceChildren(el("div", "notice error", err.message));
    sub.textContent = "Could not refresh";
  } finally {
    root.classList.remove("loading");
  }
}

function render() {
  const cats = ["All", ...new Set(markets.map((m) => m.category).filter(Boolean))];
  if (!cats.includes(category)) category = "All";
  chipBox.replaceChildren(...cats.map((name) => {
    const chip = el("button", "chip", name);
    chip.type = "button";
    chip.setAttribute("aria-pressed", String(name === category));
    chip.addEventListener("click", () => {
      category = name;
      store.set("liveCategory", name);
      render();
    });
    return chip;
  }));

  const shown = markets.filter((m) => category === "All" || m.category === category);
  const most = Math.max(...shown.map((m) => m.trades), 1);
  list.replaceChildren(...shown.map((m) => row(m, most)));
  if (!shown.length) list.append(el("div", "empty", "Nothing trading in this category right now."));
  markActive();
}

function row(m, most) {
  const item = el("a", "live-item");
  item.href = "#/market/" + encodeURIComponent(m.ticker);
  item.dataset.ticker = m.ticker;
  const price = m.last_price ?? m.yes_bid;
  const name = m.name && !m.title.includes(m.name) ? m.name : "";

  // Share of trades where the taker bought Yes vs No, as a split bar.
  const flow = el("div", "flow");
  const yes = el("span", "flow-yes");
  const no = el("span", "flow-no");
  yes.style.flexGrow = m.yes_taker_share;
  no.style.flexGrow = 1 - m.yes_taker_share;
  flow.append(yes, no);
  flow.title = Math.round(m.yes_taker_share * 100) + "% of these trades were buyers taking Yes";

  const heat = el("div", "heat");
  heat.style.width = Math.max(4, (m.trades / most) * 100) + "%";

  item.append(
    el("div", "live-main",
      el("div", "live-title", m.title),
      name ? el("div", "live-name", name) : null,
      el("div", "live-meta", m.category ? el("span", "tag", m.category) : null,
        el("span", "muted", compact(m.trades) + " trades"))),
    el("div", "live-side",
      el("div", "live-price num", cents(price)),
      flow),
    el("div", "heat-track", heat),
  );
  item.addEventListener("click", (event) => {
    event.preventDefault();
    go("/market/" + encodeURIComponent(m.ticker));
  });
  return item;
}

function markActive() {
  const match = location.hash.match(/^#\/market\/(.+)$/);
  const ticker = match ? decodeURIComponent(match[1]) : null;
  for (const item of list.querySelectorAll(".live-item")) {
    item.classList.toggle("active", item.dataset.ticker === ticker);
  }
}
