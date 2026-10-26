// A small SVG line chart: hairline grid, 2px line with a light wash, an end dot,
// and a crosshair that snaps to the nearest point and shows every value there.
import { el, svg } from "./util.js";

const MARGIN = { top: 12, right: 16, bottom: 26, left: 48 };

function niceStep(span, target) {
  const raw = span / target;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  // The nearest of 1, 2, 5 or 10 on a log scale, so the tick count stays near target.
  return (norm < 1.41 ? 1 : norm < 3.16 ? 2 : norm < 7.07 ? 5 : 10) * mag;
}

function ticks(lo, hi, target) {
  const step = niceStep(hi - lo || 1, target);
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}

/**
 * Draw points [{t: unix seconds, v: number, ...}] into container and keep it sized.
 * options:
 *   height       plot height in px (axes add to it)
 *   formatValue  value -> axis / tooltip text
 *   formatTime   unix seconds -> text
 *   tooltipRows  point -> [[label, value text], ...] shown under the main value
 *   domain       [min, max] limits for the y axis, e.g. [0, 1] for prices
 *   minSpan      the smallest range the y axis shows, so tiny wiggles stay small
 *   step         draw as a step line (a value holds until the next point)
 *   zero         emphasise y = 0 (for profit and loss)
 *   label        accessible name of the chart
 * Returns a function that redraws with new points (holding the frame meanwhile).
 */
export function lineChart(container, points, options) {
  const opts = { height: 220, step: false, zero: false, ...options };
  const wrap = el("div", "chart");
  const tip = el("div", "chart-tip");
  tip.hidden = true;
  wrap.append(tip);
  container.replaceChildren(wrap);
  let data = points;
  let width = 0;
  let focusIndex = -1;
  let layout = null;

  function draw() {
    width = Math.max(240, Math.round(wrap.clientWidth || container.clientWidth || 600));
    const height = opts.height + MARGIN.top + MARGIN.bottom;
    const plotW = width - MARGIN.left - MARGIN.right;
    const root = svg("svg", {
      width, height, viewBox: `0 0 ${width} ${height}`, role: "img", tabindex: "0",
      "aria-label": opts.label || "chart",
    });
    wrap.querySelector("svg")?.remove();
    wrap.prepend(root);
    if (!data.length) {
      layout = null;
      const text = svg("text", { x: width / 2, y: height / 2, "text-anchor": "middle", class: "chart-empty" });
      text.textContent = opts.emptyText || "No data yet";
      root.append(text);
      return;
    }

    const vs = data.map((p) => p.v);
    let lo = Math.min(...vs, ...(opts.zero ? [0] : []));
    let hi = Math.max(...vs, ...(opts.zero ? [0] : []));
    const pad = (hi - lo) * 0.15 || Math.abs(hi) * 0.1 || 0.05;
    lo -= pad;
    hi += pad;
    if (opts.minSpan && hi - lo < opts.minSpan) {
      const mid = (hi + lo) / 2;
      lo = mid - opts.minSpan / 2;
      hi = mid + opts.minSpan / 2;
    }
    if (opts.domain) {
      // Clamp to the domain, sliding the window instead of shrinking it.
      if (lo < opts.domain[0]) [lo, hi] = [opts.domain[0], Math.min(opts.domain[1], hi + opts.domain[0] - lo)];
      if (hi > opts.domain[1]) [lo, hi] = [Math.max(opts.domain[0], lo - (hi - opts.domain[1])), opts.domain[1]];
    }
    const t0 = data[0].t;
    const t1 = Math.max(data[data.length - 1].t, t0 + 1);
    const x = (t) => MARGIN.left + ((t - t0) / (t1 - t0)) * plotW;
    const y = (v) => MARGIN.top + (1 - (v - lo) / (hi - lo || 1)) * opts.height;
    layout = { x, y };

    for (const v of ticks(lo, hi, 4)) {
      const gy = y(v);
      root.append(svg("line", { x1: MARGIN.left, x2: width - MARGIN.right, y1: gy, y2: gy, class: v === 0 && opts.zero ? "chart-zero" : "chart-grid" }));
      const label = svg("text", { x: MARGIN.left - 8, y: gy + 4, "text-anchor": "end", class: "chart-axis" });
      label.textContent = opts.formatValue(v);
      root.append(label);
    }
    const tickCount = Math.max(2, Math.min(6, Math.floor(plotW / 110)));
    for (let i = 0; i <= tickCount; i++) {
      const t = t0 + ((t1 - t0) * i) / tickCount;
      const label = svg("text", {
        x: x(t), y: height - 6, class: "chart-axis",
        "text-anchor": i === 0 ? "start" : i === tickCount ? "end" : "middle",
      });
      label.textContent = opts.formatTime(t, t1 - t0);
      root.append(label);
    }
    root.append(svg("line", { x1: MARGIN.left, x2: width - MARGIN.right, y1: MARGIN.top + opts.height, y2: MARGIN.top + opts.height, class: "chart-baseline" }));

    let d = "";
    data.forEach((p, i) => {
      if (i === 0) d += `M${x(p.t)},${y(p.v)}`;
      else if (opts.step) d += `H${x(p.t)}V${y(p.v)}`;
      else d += `L${x(p.t)},${y(p.v)}`;
    });
    const last = data[data.length - 1];
    const floor = opts.zero ? y(Math.max(lo, Math.min(hi, 0))) : MARGIN.top + opts.height;
    root.append(
      svg("path", { d: `${d}V${floor}H${x(t0)}Z`, class: "chart-area" }),
      svg("path", { d, class: "chart-line" }),
      svg("circle", { cx: x(last.t), cy: y(last.v), r: 4, class: "chart-dot" }),
    );

    const cross = svg("line", { y1: MARGIN.top, y2: MARGIN.top + opts.height, class: "chart-cross", visibility: "hidden" });
    const marker = svg("circle", { r: 4, class: "chart-dot", visibility: "hidden" });
    root.append(cross, marker);
    // A transparent layer over the plot so the pointer never has to touch the line.
    const hit = svg("rect", { x: MARGIN.left, y: 0, width: plotW, height, fill: "transparent" });
    root.append(hit);

    const show = (i) => {
      focusIndex = i;
      const p = data[i];
      const px = x(p.t);
      cross.setAttribute("x1", px);
      cross.setAttribute("x2", px);
      marker.setAttribute("cx", px);
      marker.setAttribute("cy", y(p.v));
      cross.setAttribute("visibility", "visible");
      marker.setAttribute("visibility", "visible");
      tip.replaceChildren(
        el("div", "chart-tip-value", opts.formatValue(p.v, true)),
        el("div", "chart-tip-time", opts.formatTime(p.t, 0)),
        ...(opts.tooltipRows ? opts.tooltipRows(p) : []).map(([k, v]) => el("div", "chart-tip-row", el("span", "", k), el("strong", "", v))),
      );
      tip.hidden = false;
      const left = Math.min(Math.max(px + 12, 0), width - tip.offsetWidth - 4);
      tip.style.left = (px + 12 + tip.offsetWidth > width ? px - tip.offsetWidth - 12 : left) + "px";
      tip.style.top = MARGIN.top + "px";
    };
    const hide = () => {
      focusIndex = -1;
      cross.setAttribute("visibility", "hidden");
      marker.setAttribute("visibility", "hidden");
      tip.hidden = true;
    };
    const nearest = (px) => {
      let best = 0;
      for (let i = 1; i < data.length; i++) if (Math.abs(x(data[i].t) - px) < Math.abs(x(data[best].t) - px)) best = i;
      return best;
    };
    hit.addEventListener("pointermove", (event) => {
      const box = root.getBoundingClientRect();
      show(nearest(event.clientX - box.left));
    });
    hit.addEventListener("pointerleave", hide);
    root.addEventListener("blur", hide);
    root.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      const start = focusIndex < 0 ? data.length - 1 : focusIndex + (event.key === "ArrowRight" ? 1 : -1);
      show(Math.max(0, Math.min(data.length - 1, start)));
    });
  }

  const observer = new ResizeObserver(() => {
    if (Math.round(wrap.clientWidth) !== width) draw();
  });
  observer.observe(wrap);
  draw();

  const update = (next) => {
    data = next;
    draw();
  };
  update.destroy = () => observer.disconnect();
  return update;
}

export function timeLabel(t, span) {
  const d = new Date(t * 1000);
  if (span > 2 * 86400) return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  if (span === 0) return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}
