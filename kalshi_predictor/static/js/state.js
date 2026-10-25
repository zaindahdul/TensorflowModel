// State shared by the views, and navigation helpers.
import { store } from "./util.js";

export const state = {
  info: { window: 50, horizon_s: 60, threshold: 0.01 },
  contracts: store.get("contracts", 10),
};

export function setContracts(n) {
  state.contracts = n;
  store.set("contracts", n);
}

export function go(path) {
  location.hash = "#" + path;
}

// Open whatever was typed or pasted: a market, event or series ticker, or a kalshi.com link.
export function openTarget(target) {
  go("/open/" + encodeURIComponent(target.trim()));
}
