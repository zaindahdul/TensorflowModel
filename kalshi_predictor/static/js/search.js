// The top bar search box: search Kalshi by name as you type, or open a pasted link or ticker.
import * as api from "./api.js";
import { go, openTarget } from "./state.js";
import { cents, compact, el } from "./util.js";

const DEBOUNCE_MS = 220;

let input, list;
let options = []; // [{node, choose}]
let active = -1;
let seq = 0;
let debounce;
let shownFor = ""; // the text the listed options are for
let enterPending = false; // Enter was pressed before results arrived

export function setup(form) {
  input = form.querySelector("input");
  list = el("div", "omni-list");
  list.id = "omni-list";
  list.setAttribute("role", "listbox");
  list.hidden = true;
  form.append(list);
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-controls", list.id);
  input.setAttribute("aria-expanded", "false");

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    if (shownFor === text && active >= 0) return options[active].choose();
    if (/kalshi\.com\//i.test(text)) return finish(() => openTarget(text));
    enterPending = true;
    clearTimeout(debounce);
    update();
  });
  input.addEventListener("input", () => {
    clearTimeout(debounce);
    debounce = setTimeout(update, DEBOUNCE_MS);
  });
  input.addEventListener("focus", () => { if (input.value.trim()) update(); });
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!options.length) return;
      const step = event.key === "ArrowDown" ? 1 : -1;
      highlight((active + step + options.length) % options.length);
    } else if (event.key === "Escape") {
      close();
      input.blur();
    }
  });
  document.addEventListener("pointerdown", (event) => { if (!form.contains(event.target)) close(); });
}

export function focus() {
  input.focus();
  input.select();
}

// Pasted links, and things shaped like tickers, can be opened directly.
function looksLikeTicker(text) {
  return /kalshi\.com\//i.test(text) || /^KX[A-Z0-9]/i.test(text);
}

async function update() {
  const text = input.value.trim();
  const mine = ++seq;
  if (!text) return close();
  const direct = !/\s/.test(text) && text.length >= 3 ? directOption(text) : null;
  if (/kalshi\.com\//i.test(text)) {
    shownFor = text;
    return show([direct]);
  }
  show([looksLikeTicker(text) ? direct : null, el("div", "omni-note", "Searching Kalshi...")].filter(Boolean), true);
  let events = [];
  try {
    events = (await api.get("/api/search", { q: text })).events;
  } catch (err) {
    if (mine === seq) show([direct, el("div", "omni-note", err.message)].filter(Boolean));
    return;
  }
  if (mine !== seq) return; // a newer search is running
  const rows = events.slice(0, 8).flatMap(eventOptions);
  if (direct) (looksLikeTicker(text) ? rows.unshift(direct) : rows.push(direct));
  if (!events.length) rows.push(el("div", "omni-note", "No open markets match “" + text + "”."));
  show(rows);
  shownFor = text;
  if (enterPending && options.length) options[0].choose();
  enterPending = false;
}

function directOption(text) {
  const ticker = text.split(/[?#]/)[0].replace(/\/+$/, "").split("/").pop().toUpperCase();
  return option(
    el("div", "omni-direct", el("span", "omni-key", "Open"), el("span", "mono", ticker)),
    () => openTarget(text),
  );
}

function eventOptions(event) {
  const head = option(
    el("div", "omni-event",
      el("div", "omni-title", event.title),
      el("div", "omni-meta",
        event.category ? el("span", "tag", event.category) : null,
        el("span", "muted", [event.subtitle, compact(event.volume) + " traded"].filter(Boolean).join(" · ")),
        event.markets.length > 1 ? el("span", "muted", "· " + event.markets.length + " markets") : null)),
    () => (event.markets.length === 1
      ? go("/market/" + encodeURIComponent(event.markets[0].ticker))
      : openTarget(event.event_ticker)),
  );
  const markets = event.markets.length > 1
    ? event.markets.slice(0, 3).map((m) => option(
      el("div", "omni-market",
        el("span", "omni-market-name", m.name),
        el("span", "num", cents(m.last_price ?? m.yes_bid))),
      () => go("/market/" + encodeURIComponent(m.ticker)),
    ))
    : [];
  return [head, ...markets];
}

function option(content, onChoose) {
  const node = el("div", "omni-option", content);
  node.setAttribute("role", "option");
  const entry = { node, choose: () => finish(onChoose) };
  node.addEventListener("pointerdown", (event) => event.preventDefault()); // keep focus in the box
  node.addEventListener("click", entry.choose);
  node.addEventListener("pointermove", () => highlight(options.indexOf(entry)));
  node._entry = entry;
  return node;
}

function show(nodes, pending = false) {
  options = nodes.filter((n) => n._entry).map((n) => n._entry);
  list.replaceChildren(...nodes);
  list.classList.toggle("pending", pending);
  list.hidden = !nodes.length;
  input.setAttribute("aria-expanded", String(!list.hidden));
  highlight(options.length ? 0 : -1);
}

function highlight(i) {
  active = i;
  options.forEach((o, j) => o.node.setAttribute("aria-selected", String(j === i)));
  if (i >= 0) options[i].node.scrollIntoView({ block: "nearest" });
}

function close() {
  seq++;
  shownFor = "";
  enterPending = false;
  list.hidden = true;
  input.setAttribute("aria-expanded", "false");
  options = [];
  active = -1;
}

function finish(action) {
  close();
  input.value = "";
  input.blur();
  action();
}
