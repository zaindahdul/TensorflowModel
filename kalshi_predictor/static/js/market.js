// Markets tab: the welcome screen, a market's page, and multi-market results.
import * as api from "./api.js";
import { go, setContracts, state } from "./state.js";
import { best, capitalize, cents, copyText, dirBadge, el, icon, percent, toast } from "./util.js";

export const tab = "market";
export const sidebar = true;

// Predictions made while opening a link, handed to the market page so it does
// not predict (and paper trade) the same market twice.
const handoff = new Map();
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
  );
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

function marketPage(page, ticker) {
  const head = el("div", "page-head");
  const side = el("div", "grid");
  page.append(head, el("div", "market-grid", el("div", "grid market-main"), side));

  const panel = el("div", "card prediction");
  side.append(panel);
  let alive = true;

  async function run(result) {
    panel.classList.add("loading");
    try {
      result = result || (await predict(ticker)).results[0];
      if (!alive) return;
      renderHead(head, result);
      renderPrediction(panel, result, () => run());
    } catch (err) {
      if (alive) panel.replaceChildren(el("div", "notice error", err.message));
    } finally {
      panel.classList.remove("loading");
    }
  }

  panel.append(el("div", "empty", el("span", "spinner"), " Reading the latest trades..."));
  run(handoff.get(ticker));
  handoff.delete(ticker);
  teardown = () => { alive = false; };
}

function renderHead(head, result) {
  const copy = el("button", "btn ghost small", icon("copy"), "Copy ticker");
  copy.addEventListener("click", async () => toast((await copyText(result.ticker)) ? "Copied " + result.ticker : "Copy failed"));
  const titles = el("div", "",
    el("h1", "", result.title || result.name || result.ticker),
    result.name && result.name !== result.title ? el("p", "lead", result.name) : null,
    el("div", "ticker", result.ticker));
  head.replaceChildren(titles, el("div", "head-actions", copy));
}

function renderPrediction(panel, result, again) {
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
  parts.push(el("div", "predict-row", button, el("label", "size-label", size, " contracts")));
  panel.replaceChildren(...parts);
}
