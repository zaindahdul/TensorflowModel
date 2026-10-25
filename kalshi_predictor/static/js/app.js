// App shell: theme, the search box, and a hash router that mounts one view at a time.
import * as api from "./api.js";
import { icon, store } from "./util.js";
import { go, openTarget, state } from "./state.js";
import * as market from "./market.js";
import * as sidebar from "./sidebar.js";

// path pattern -> view module; the first match wins.
const routes = [
  [/^\/market\/(.+)$/, market, (m) => ({ ticker: decodeURIComponent(m[1]) })],
  [/^\/open\/(.+)$/, market, (m) => ({ target: decodeURIComponent(m[1]) })],
  [/^\/?$/, market, () => ({})],
];

const view = document.getElementById("view");
let unmount = null;

function route() {
  const path = location.hash.replace(/^#/, "") || "/";
  for (const [pattern, module, params] of routes) {
    const m = path.match(pattern);
    if (!m) continue;
    if (unmount) unmount();
    view.replaceChildren();
    for (const a of document.querySelectorAll(".tabs a")) {
      if (a.dataset.tab === module.tab) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
    document.querySelector(".layout").classList.toggle("no-sidebar", !module.sidebar);
    unmount = module.mount(view, params(m)) || null;
    window.scrollTo(0, 0);
    return;
  }
  go("/");
}

function setupTheme() {
  const button = document.getElementById("theme");
  const dark = () =>
    document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
  const paint = () => button.replaceChildren(icon(dark() ? "sun" : "moon"));
  button.addEventListener("click", () => {
    const next = dark() ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store.set("theme", next);
    paint();
    window.dispatchEvent(new Event("themechange"));
  });
  paint();
}

function setupSearch() {
  const form = document.getElementById("omni");
  const input = document.getElementById("omni-input");
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (input.value.trim()) {
      openTarget(input.value);
      input.blur();
    }
  });
}

async function start() {
  setupTheme();
  setupSearch();
  // A link like /?target=TICKER (the bookmarklet uses this) opens that market.
  const preset = new URLSearchParams(location.search).get("target");
  if (preset) {
    history.replaceState(null, "", location.pathname + "#/open/" + encodeURIComponent(preset));
  }
  try {
    state.info = await api.get("/api/info");
  } catch (err) {
    // keep the defaults; views report their own errors
  }
  window.addEventListener("hashchange", route);
  route();
  sidebar.setup(document.getElementById("sidebar"));
}

start();
