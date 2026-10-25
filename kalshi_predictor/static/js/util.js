// Small DOM and formatting helpers shared by every view.

export function el(tag, className, ...children) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

const SVG_NS = "http://www.w3.org/2000/svg";

export function svg(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

// Feather-style icons, drawn with the current text color.
const ICONS = {
  search: "M11 19a8 8 0 1 1 0-16 8 8 0 0 1 0 16zM21 21l-4.35-4.35",
  sun: "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
  moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
  up: "M12 19V5M5 12l7-7 7 7",
  down: "M12 5v14M19 12l-7 7-7-7",
  flat: "M5 12h14",
  external: "M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3",
  copy: "M20 9h-9a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2v-9a2 2 0 0 0-2-2zM5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1",
  refresh: "M23 4v6h-6M1 20v-6h6M3.5 9a9 9 0 0 1 14.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0 0 20.5 15",
  star: "M12 2l3.1 6.3 6.9 1-5 4.9 1.2 6.8-6.2-3.2-6.2 3.2L7 14.2 2 9.3l6.9-1z",
  bolt: "M13 2L3 14h9l-1 8 10-12h-9z",
  download: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3",
  x: "M18 6L6 18M6 6l12 12",
  check: "M20 6L9 17l-5-5",
};

export function icon(name, extraClass = "") {
  const node = svg("svg", {
    viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "2",
    "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true",
    class: ("icon " + extraClass).trim(),
  });
  node.append(svg("path", { d: ICONS[name] }));
  return node;
}

export function cents(dollars, digits = 0) {
  if (dollars === null || dollars === undefined) return "–";
  const c = dollars * 100;
  const text = Math.abs(c - Math.round(c)) < 1e-6 ? String(Math.round(c)) : c.toFixed(Math.max(digits, 1));
  return text + "¢";
}

export function percent(p, digits = 0) {
  return (p * 100).toFixed(digits) + "%";
}

export function money(dollars) {
  const sign = dollars > 0 ? "+" : dollars < 0 ? "−" : "";
  return sign + "$" + Math.abs(dollars).toFixed(2);
}

export function moneyClass(dollars) {
  return dollars > 0 ? "gain" : dollars < 0 ? "loss" : "";
}

export function compact(n) {
  if (n === null || n === undefined) return "–";
  const abs = Math.abs(n);
  if (abs >= 1e9) return (n / 1e9).toFixed(1).replace(/\.0$/, "") + "B";
  if (abs >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
  if (abs >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, "") + "K";
  return String(Math.round(n));
}

export function ago(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 5) return "just now";
  if (s < 60) return Math.round(s) + "s ago";
  if (s < 3600) return Math.round(s / 60) + " min ago";
  if (s < 48 * 3600) return Math.round(s / 3600) + "h ago";
  return Math.round(s / 86400) + "d ago";
}

export function untilText(iso) {
  const s = (new Date(iso).getTime() - Date.now()) / 1000;
  if (s <= 0) return "closed";
  if (s < 60) return Math.ceil(s) + "s left";
  if (s < 3600) return Math.floor(s / 60) + "m " + String(Math.floor(s % 60)).padStart(2, "0") + "s left";
  if (s < 48 * 3600) return Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m left";
  return "closes " + new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function capitalize(s) {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}

// The class the model thinks likeliest, and its probability.
export function best(probs) {
  return Object.entries(probs).reduce((a, b) => (b[1] > a[1] ? b : a));
}

export function dirBadge(direction, text, big = false) {
  const name = direction || "flat";
  return el("span", "dir " + name + (big ? " big" : ""), icon(name), text ?? name.toUpperCase());
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (err) {
    const area = el("textarea");
    area.value = text;
    area.style.cssText = "position:fixed;opacity:0";
    document.body.append(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  }
}

let toastTimer;
export function toast(message) {
  const box = document.getElementById("toast");
  box.textContent = message;
  box.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.remove("show"), 2200);
}

// localStorage that never throws (private windows, blocked storage).
export const store = {
  get(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch (err) {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (err) {
      // the page works without it
    }
  },
};
