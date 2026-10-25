// Markets tab: the welcome screen, a market's page, and multi-market results.
import * as api from "./api.js";
import { go, setContracts, state } from "./state.js";
import { lineChart, timeLabel } from "./chart.js";
import {
  best, capitalize, cents, compact, copyText, dirBadge, el, icon, money, moneyClass, percent, store, toast, untilText,
} from "./util.js";

export const tab = "market";
export const sidebar = true;

// Predictions made while opening a link, handed to the market page so it does
// not predict (and paper trade) the same market twice.
const handoff = new Map();

export function handOff(result) {
  handoff.set(result.ticker, result);
}
let teardown = null; // stops the open market page's requests and timers

export function mount(root, params) {
  const page = el("div", "page");
  root.append(page);
  if (params.target) openTarget(page, params.target);
  else if (params.ticker) marketPage(page, params.ticker);
  else welcome(page);
  return () => {
    if (teardown) teardown();
    teardown = null;
  };
}

function welcome(page) {
  const steps = el("div", "grid welcome-steps");
  for (const [title, text] of [
    ["Pick a live market", "The list on the left is what is trading most on Kalshi right now. Those are the markets the model reads best."],
    ["Or search by name", "Type a team, a coin or a topic in the search box. You can also paste any kalshi.com link or ticker."],
    ["Watch the track record", "Every prediction is paper traded and checked a minute later, so you can see whether the model is any good."],
  ]) {
    steps.append(el("div", "card", el("h2", "", title), el("p", "lead", text)));
  }
  page.append(
    el("div", "page-head", el("div", "",
      el("h1", "", "What happens in the next minute?"),
      el("p", "lead", "A neural network reads a market's last " + state.info.window +
        " trades and estimates whether its Yes price goes up, down or stays flat over the next " +
        state.info.horizon_s + " seconds."))),
    steps,
    bookmarkletCard(),
  );
}

// A bookmark that sends the kalshi.com page you are on to this dashboard.
function bookmarkletCard() {
  const code = "javascript:(()=>{window.open(" + JSON.stringify(location.origin + "/?target=") +
    "+encodeURIComponent(location.href),'_blank')})()";
  const link = el("a", "bookmarklet", icon("bolt"), "Predict on dashboard");
  link.href = code;
  link.addEventListener("click", (event) => {
    event.preventDefault();
    toast("Drag it to your bookmarks bar instead of clicking it here");
  });
  return el("section", "card bookmarklet-card",
    el("div", "", el("h2", "", "One click from kalshi.com"),
      el("p", "lead", "Drag this button to your bookmarks bar. Then, on any market page on kalshi.com, " +
        "click the bookmark to open that market here with a fresh prediction. No copying tickers.")),
    link);
}

async function openTarget(page, target) {
  page.append(el("div", "empty", el("span", "spinner"), " Looking up ", target, "..."));
  let body;
  try {
    body = await predict(target);
  } catch (err) {
    page.replaceChildren(el("div", "notice error", err.message));
    return;
  }
  if (body.results.length === 1) {
    const result = body.results[0];
    handoff.set(result.ticker, result);
    history.replaceState(null, "", "#/market/" + encodeURIComponent(result.ticker));
    page.replaceChildren();
    marketPage(page, result.ticker);
    return;
  }
  const list = el("div", "result-list");
  for (const result of body.results) list.append(resultCard(result));
  page.replaceChildren(
    el("div", "page-head", el("div", "",
      el("h1", "", body.title || body.ticker),
      el("p", "lead", body.results.length + " markets. Pick one to see its chart and order book."))),
    body.note ? el("div", "notice warn", body.note) : null,
    list,
  );
  page.querySelector(".notice")?.style.setProperty("margin-bottom", "16px");
}

function predict(target) {
  return api.post("/api/predict", { target, contracts: state.contracts });
}

function resultCard(result) {
  const card = el("button", "card result-card");
  card.type = "button";
  card.style.textAlign = "left";
  card.append(el("div", "title", result.name || result.ticker), el("div", "ticker", result.ticker));
  if (result.probs) {
    const [label, p] = best(result.probs);
    card.append(el("div", "verdict", dirBadge(label), el("span", "ink-2", percent(p) + " likely")), probBars(result.probs));
  } else {
    card.append(el("p", "muted", "No prediction: " + result.problem + "."));
  }
  card.addEventListener("click", () => {
    handoff.set(result.ticker, result);
    go("/market/" + encodeURIComponent(result.ticker));
  });
  return card;
}

export function probBars(probs) {
  const box = el("div", "probs");
  for (const label of ["up", "flat", "down"]) {
    const fill = el("div", "fill " + label);
    fill.style.width = (probs[label] * 100).toFixed(1) + "%";
    box.append(el("div", "prob-row",
      el("span", "label", capitalize(label)), el("div", "track", fill), el("span", "pct", percent(probs[label], 1))));
  }
  return box;
}

const DETAIL_REFRESH_MS = 15000;

function marketPage(page, ticker) {
  const head = el("div", "page-head");
  const main = el("div", "grid market-main");
  const side = el("div", "grid");
  page.append(head, el("div", "market-grid", main, side));

  const quotes = el("div", "quotes");
  const chartBox = el("div", "chart-box");
  const ranges = el("div", "segmented");
  const chartCard = el("section", "card",
    el("div", "card-head", el("div", "", el("h2", "", "Yes price"), el("div", "card-sub", "Last traded price")), ranges),
    chartBox);
  const bookBox = el("div", "");
  const siblingsBox = el("div", "");
  const rulesBox = el("div", "");
  main.append(quotes, chartCard, el("div", "grid two-up", bookBox, siblingsBox), rulesBox);
  const panel = el("div", "card prediction");
  const calls = el("div", "");
  side.append(panel, calls);

  let alive = true;
  let span = store.get("span", "6h");
  let detail = null;
  let chart = null;
  let refreshTimer = null;
  let callsTimer = null;
  let autoTimer = null;
  let autoAt = 0; // when the next automatic prediction runs
  let auto = store.get("autoPredict", false);
  const clock = setInterval(() => {
    if (detail) renderCountdown(head, detail.market);
    const left = panel.querySelector(".auto-left");
    if (left) left.textContent = auto && autoAt ? "next in " + Math.max(0, Math.ceil((autoAt - Date.now()) / 1000)) + "s" : "";
  }, 1000);

  for (const name of state.info.history_spans || ["1h", "6h", "1d", "1w"]) {
    const b = el("button", "", name.toUpperCase());
    b.type = "button";
    b.dataset.span = name;
    b.addEventListener("click", () => {
      span = name;
      store.set("span", name);
      loadDetail();
    });
    ranges.append(b);
  }

  async function loadDetail() {
    clearTimeout(refreshTimer);
    for (const b of ranges.children) b.setAttribute("aria-pressed", String(b.dataset.span === span));
    chartBox.classList.add("loading"); // keep the old chart visible meanwhile
    try {
      const next = await api.get("/api/market", { ticker, span });
      if (!alive) return;
      detail = next;
      renderHead(head, detail.market);
      renderQuotes(quotes, detail.market);
      renderBook(bookBox, detail.orderbook);
      renderSiblings(siblingsBox, detail.event, ticker);
      renderRules(rulesBox, detail.market);
      const points = detail.history.filter((c) => c.price !== null).map((c) => ({ ...c, v: c.price }));
      if (!chart) {
        chart = lineChart(chartBox, points, {
          label: "Yes price over the last " + span,
          height: 240, step: true, domain: [0, 1], minSpan: 0.1,
          formatValue: (v, precise) => cents(v, precise ? 1 : 0),
          formatTime: timeLabel,
          emptyText: "No trades in this range",
          tooltipRows: (p) => [
            ["Yes bid / ask", cents(p.yes_bid) + " / " + cents(p.yes_ask)],
            ["Contracts traded", compact(p.volume)],
          ],
        });
      } else {
        chart(points);
      }
    } catch (err) {
      if (alive && !detail) chartBox.replaceChildren(el("div", "notice error", err.message));
    } finally {
      chartBox.classList.remove("loading");
      if (alive) refreshTimer = setTimeout(loadDetail, DETAIL_REFRESH_MS);
    }
  }

  async function run(result) {
    clearTimeout(autoTimer);
    autoAt = 0;
    panel.classList.add("loading");
    try {
      result = result || (await predict(ticker)).results[0];
      if (!alive) return;
      if (!detail) renderHead(head, { ticker, title: result.title, name: result.name });
      renderPrediction(panel, result, { again: () => run(), auto, setAuto });
      loadCalls();
    } catch (err) {
      if (alive) panel.replaceChildren(el("div", "notice error", err.message));
    } finally {
      panel.classList.remove("loading");
      if (alive && auto) scheduleAuto();
    }
  }

  function scheduleAuto() {
    clearTimeout(autoTimer);
    autoAt = Date.now() + state.info.horizon_s * 1000;
    autoTimer = setTimeout(() => run(), state.info.horizon_s * 1000);
  }

  function setAuto(on) {
    auto = on;
    store.set("autoPredict", on);
    if (on) scheduleAuto();
    else {
      clearTimeout(autoTimer);
      autoAt = 0;
    }
  }

  // This market's past predictions; polled while any is still being checked.
  async function loadCalls() {
    clearTimeout(callsTimer);
    try {
      const data = await api.get("/api/history", { ticker, limit: 8 });
      if (!alive) return;
      renderCalls(calls, data);
      if (data.summary.pending) callsTimer = setTimeout(loadCalls, 2000);
    } catch (err) {
      // the panel above already shows connection problems
    }
  }

  panel.append(el("div", "empty", el("span", "spinner"), " Reading the latest trades..."));
  run(handoff.get(ticker));
  handoff.delete(ticker);
  loadDetail();
  teardown = () => {
    alive = false;
    clearTimeout(refreshTimer);
    clearTimeout(callsTimer);
    clearTimeout(autoTimer);
    clearInterval(clock);
    chart?.destroy();
  };
}

function renderHead(head, m) {
  const copy = el("button", "btn ghost small", icon("copy"), "Copy ticker");
  copy.addEventListener("click", async () => toast((await copyText(m.ticker)) ? "Copied " + m.ticker : "Copy failed"));
  const actions = el("div", "head-actions", copy);
  if (m.url) {
    const link = el("a", "btn small", "Open on Kalshi", icon("external"));
    link.href = m.url;
    link.target = "_blank";
    link.rel = "noopener";
    actions.append(link);
  }
  const showName = m.name && !(m.title || "").includes(m.name);
  head.replaceChildren(
    el("div", "",
      el("div", "eyebrow", m.category ? el("span", "tag", m.category) : null, el("span", "ticker", m.ticker),
        el("span", "countdown muted")),
      el("h1", "", m.title || m.name || m.ticker),
      showName ? el("p", "lead", m.name) : null),
    actions,
  );
  renderCountdown(head, m);
}

function renderCountdown(head, m) {
  const box = head.querySelector(".countdown");
  if (box && m.close_time) box.textContent = "\u00b7 " + untilText(m.close_time);
}

const BOOK_LEVELS = 6;

// One ladder: offers to sell Yes on top (each is a No bid at 1 - price), bids
// for Yes underneath, with bars sized by how many contracts rest there.
function renderBook(box, book) {
  const asks = book.no.slice(0, BOOK_LEVELS).map(([p, q]) => [1 - p, q]).reverse();
  const bids = book.yes.slice(0, BOOK_LEVELS);
  const most = Math.max(1, ...asks.map(([, q]) => q), ...bids.map(([, q]) => q));
  const row = ([price, size], side) => {
    const bar = el("div", "book-bar " + side);
    bar.style.width = ((size / most) * 100).toFixed(1) + "%";
    return el("div", "book-row", el("span", "num", cents(price)), el("span", "num muted", compact(size)), el("div", "book-track", bar));
  };
  const spread = asks.length && bids.length ? asks[asks.length - 1][0] - bids[0][0] : null;
  const card = el("section", "card book",
    el("div", "card-head", el("div", "", el("h2", "", "Order book"), el("div", "card-sub", "Yes price \u00b7 contracts resting"))),
    el("div", "book-key", el("span", "key no"), "Offers to sell Yes (No bids)"),
    ...asks.map((level) => row(level, "no")),
    el("div", "book-spread", spread !== null ? "spread " + cents(spread) : "no spread"),
    ...bids.map((level) => row(level, "yes")),
    el("div", "book-key", el("span", "key yes"), "Bids to buy Yes"),
  );
  if (!asks.length && !bids.length) card.append(el("p", "muted", "Nobody has an order resting in this market."));
  box.replaceChildren(card);
}

function renderSiblings(box, event, current) {
  if (!event || event.markets.length < 2) {
    box.replaceChildren();
    return;
  }
  const list = el("div", "siblings");
  for (const m of event.markets) {
    const item = el("a", "sibling" + (m.ticker === current ? " active" : ""),
      el("span", "sibling-name", m.name || m.title), el("span", "num", cents(m.last_price ?? m.yes_bid)));
    item.href = "#/market/" + encodeURIComponent(m.ticker);
    list.append(item);
  }
  box.replaceChildren(el("section", "card",
    el("div", "card-head", el("div", "", el("h2", "", "In this event"), el("div", "card-sub", event.title))),
    list));
}

function renderRules(box, m) {
  if (!m.rules) {
    box.replaceChildren();
    return;
  }
  const open = box.querySelector("details")?.open ?? false;
  const details = el("details", "card rules", el("summary", "", "How this market resolves"), el("p", "", m.rules));
  details.open = open;
  box.replaceChildren(details);
}

function renderQuotes(box, m) {
  const tile = (label, value, sub) => el("div", "quote",
    el("div", "quote-label", label), el("div", "quote-value", value), sub ? el("div", "quote-sub", sub) : null);
  const spread = m.yes_ask !== null && m.yes_bid !== null ? m.yes_ask - m.yes_bid : null;
  box.replaceChildren(
    tile("Last price", cents(m.last_price), "chance of Yes, per the market"),
    tile("Yes bid / ask", cents(m.yes_bid) + " / " + cents(m.yes_ask), spread !== null ? "spread " + cents(spread) : null),
    tile("24h volume", compact(m.volume_24h), "contracts"),
    tile("Open interest", compact(m.open_interest), "contracts held"),
  );
}

function renderPrediction(panel, result, { again, auto, setAuto }) {
  const horizon = state.info.horizon_s;
  const size = el("input", "field");
  Object.assign(size, { type: "number", min: 1, max: 100000, step: 1, value: state.contracts });
  size.setAttribute("aria-label", "Paper trade size in contracts");
  size.addEventListener("change", () => {
    const n = Math.round(Number(size.value));
    if (n >= 1 && n <= 100000) setContracts(n);
    size.value = state.contracts;
  });
  const button = el("button", "btn primary", icon("refresh"), "Predict again");
  button.addEventListener("click", again);

  const parts = [el("div", "card-head", el("h2", "", "Next " + horizon + " seconds"), el("span", "card-sub", "model estimate"))];
  if (result.problem) {
    parts.push(el("p", "muted", "No prediction: " + result.problem + "."));
  } else {
    const [label, p] = best(result.probs);
    parts.push(
      el("div", "verdict-line", dirBadge(label, null, true), el("div", "",
        el("div", "verdict-pct", percent(p)), el("div", "card-sub", "chance of " + (label === "flat" ? "no move" : "a move " + label)))),
      probBars(result.probs),
      el("p", "card-sub", "Up or down means the Yes price moves at least " + cents(state.info.threshold) +
        " within " + horizon + "s of the last trade (" + cents(result.price) + ", " +
        new Date(result.last_trade).toLocaleTimeString() + ")."),
    );
    if (result.age_min * 60 > horizon) {
      parts.push(el("div", "notice warn", "This market is quiet: its last trade was " +
        Math.round(result.age_min) + " min ago, so that " + horizon + "-second window has already passed."));
    }
    const t = result.paper_trade;
    if (t) {
      parts.push(el("p", "paper", t.skipped
        ? "Paper trade: none, because " + t.skipped + "."
        : "Paper trade: bought " + t.contracts.toLocaleString() + " " + capitalize(t.side) + " at " +
          cents(t.entry_price) + " (fee $" + t.entry_fee.toFixed(2) + "), selling at the bid in " + horizon + "s."));
    }
  }
  const toggle = el("input");
  toggle.type = "checkbox";
  toggle.checked = auto;
  toggle.addEventListener("change", () => setAuto(toggle.checked));
  parts.push(
    el("div", "predict-row", button, el("label", "size-label", size, " contracts")),
    el("div", "auto-row", el("label", "switch", toggle, "Predict again every " + horizon + "s"), el("span", "auto-left muted num")),
  );
  panel.replaceChildren(...parts);
}

export function outcomeBadge(correct) {
  const badge = el("span", "verdict-badge " + (correct ? "right" : "wrong"), icon(correct ? "check" : "x"));
  badge.title = correct ? "Right" : "Wrong";
  badge.setAttribute("role", "img");
  badge.setAttribute("aria-label", badge.title);
  return badge;
}

function renderCalls(box, data) {
  const s = data.summary;
  if (!s.predictions) {
    box.replaceChildren();
    return;
  }
  const rows = data.entries.map((e) => {
    let result;
    if (e.outcome) {
      result = el("span", "call-result", el("span", "muted", "→"), dirBadge(e.outcome.actual), outcomeBadge(e.outcome.correct));
    } else if (e.error) {
      result = el("span", "muted", "could not check");
    } else {
      const wait = Math.max(0, Math.ceil(e.due - Date.now() / 1000));
      result = el("span", "muted", el("span", "spinner"), wait > 0 ? " checking in " + wait + "s" : " checking now");
    }
    const pnl = e.trade.pnl;
    return el("div", "call",
      el("span", "muted num", new Date(e.created * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" })),
      dirBadge(e.predicted),
      result,
      el("span", "num " + (pnl === undefined ? "muted" : moneyClass(pnl)), pnl === undefined ? "" : money(pnl)));
  });
  const parts = [s.correct + " of " + s.checked + " right"];
  if (s.trades) parts.push(money(s.pnl) + " paper P&L");
  box.replaceChildren(el("section", "card",
    el("div", "card-head", el("h2", "", "Calls on this market"), el("span", "card-sub", s.checked ? parts.join(" \u00b7 ") : "")),
    el("div", "calls", ...rows)));
}
