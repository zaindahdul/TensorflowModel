"""Local web dashboard: browse live Kalshi markets, see predictions, track them.

    python -m kalshi_predictor dashboard

Opens http://127.0.0.1:8765 in your browser. Only this computer can reach it.
"""
import argparse
import json
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd
import requests

from . import kalshi, scan, tracker
from .predict import analyze_markets, load

STATIC = Path(__file__).parent / "static"
HOST = "127.0.0.1"
PORTS = range(8765, 8775)
MAX_CONTRACTS = 100_000
MAX_EVENT_MARKETS = 16  # events like "BTC price at 7pm" have hundreds of markets
LIVE_CACHE_S = 5
# Spelled out because Windows can map .js to text/plain in the registry.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}

meta = model = None
live_cache = {"at": 0.0, "body": None}
live_lock = threading.Lock()


class BadRequest(Exception):
    """A problem with what was asked for, shown to the user as is."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def jsonable(result):
    """A prediction from analyze_markets with its timestamps as ISO strings."""
    return {k: v.isoformat() if isinstance(v, pd.Timestamp) else v for k, v in result.items()}


def parse_contracts(value):
    try:
        contracts = int(value)
    except (TypeError, ValueError):
        contracts = 0
    if not 1 <= contracts <= MAX_CONTRACTS:
        raise BadRequest(f"Paper trade size must be a whole number from 1 to {MAX_CONTRACTS:,}.")
    return contracts


def tradeable_first(markets):
    """Open markets before settled ones, busiest first."""
    return sorted(markets, key=lambda m: (bool(m.get("result")), -float(m.get("volume_24h_fp") or 0)))


def predict(target, contracts, source="manual"):
    """Predict a market, event or series named by a ticker or link, and paper
    trade each prediction with this many contracts (see tracker.py)."""
    target = (target or "").strip()
    if not target:
        raise BadRequest("Search for a market, or paste a Kalshi link or ticker.")
    contracts = parse_contracts(contracts)
    ticker, markets = kalshi.find_markets(target)
    if not markets and not kalshi.parse_ticker(target).startswith("KX"):
        # Not a ticker or link Kalshi knows, so treat it as a search, e.g. "packers".
        found = kalshi.search(target, limit=1)
        if found:
            ticker, markets = kalshi.find_markets(found[0]["event_ticker"])
    quoted_at = time.time()  # the bids and asks in markets are from now
    if not markets:
        raise BadRequest(f"Nothing on Kalshi matches {target!r}. Try searching by name.", 404)
    note, title = None, markets[0].get("title", "")
    if len(markets) > 1:
        event = kalshi.get_or_none(f"/events/{markets[0]['event_ticker']}") or {}
        title = (event.get("event") or {}).get("title") or title
    if len(markets) > MAX_EVENT_MARKETS:
        note = f"Showing the {MAX_EVENT_MARKETS} most traded of {len(markets)} markets in this event."
    markets = tradeable_first(markets)[:MAX_EVENT_MARKETS]
    cats = kalshi.categories(markets)
    results = []
    for market, result in zip(markets, analyze_markets(model, meta, markets)):
        result["title"] = market.get("title", "")
        result["market"] = kalshi.market_summary(market, cats.get(kalshi.series_of(market), ""))
        if "probs" in result:
            entry = tracker.record(meta, market, result, contracts, quoted_at, source=source)
            result["paper_trade"] = entry["trade"]
            result["entry_id"] = entry["id"]
        results.append(jsonable(result))
    return {"ticker": ticker, "title": title, "results": results, "note": note}


def run_scan(markets, contracts, record):
    """The busiest live markets ranked by model confidence, optionally paper traded."""
    try:
        markets = max(1, min(int(markets), 40))
    except (TypeError, ValueError):
        markets = 20
    contracts = parse_contracts(contracts) if record else None
    rows, raw, span_s = scan.scan(model, meta, markets)
    quoted_at = time.time()
    for row, market in zip(rows, raw):
        result = row["prediction"]
        if record and "probs" in result:
            result["title"] = market.get("title", "")
            row["paper_trade"] = tracker.record(meta, market, result, contracts, quoted_at, source="scan")["trade"]
        row["prediction"] = jsonable(result)
    return {"rows": rows, "span_s": span_s, "scanned_at": quoted_at}


def live(limit):
    """Busiest markets right now. Cached briefly so several open tabs share one lookup."""
    with live_lock:
        if live_cache["body"] is None or time.time() - live_cache["at"] > LIVE_CACHE_S:
            markets, span_s = kalshi.live_markets(limit=40)
            live_cache.update(at=time.time(), body={"markets": markets, "span_s": span_s})
        body = live_cache["body"]
    return dict(body, markets=body["markets"][:limit])


def market_detail(ticker, span):
    """Everything the market view shows: quotes, order book, price history and the
    other markets in its event."""
    if span not in kalshi.HISTORY_RANGES:
        raise BadRequest(f"span must be one of {', '.join(kalshi.HISTORY_RANGES)}")
    found = kalshi.get_or_none(f"/markets/{kalshi.parse_ticker(ticker)}")
    if not found:
        raise BadRequest(f"Kalshi has no market called {ticker}.", 404)
    m = found["market"]
    with ThreadPoolExecutor(4) as pool:
        book = pool.submit(kalshi.orderbook, m["ticker"])
        history = pool.submit(kalshi.price_history, m["ticker"], m["event_ticker"], span)
        event = pool.submit(kalshi.get_or_none, f"/events/{m['event_ticker']}")
        series = pool.submit(kalshi.series_info, kalshi.series_of(m))
    series = series.result()
    category = series.get("category", "")
    siblings = tradeable_first((event.result() or {}).get("markets", []))[:MAX_EVENT_MARKETS]
    return {
        "market": dict(
            kalshi.market_summary(m, category),
            rules=m.get("rules_primary", ""),
            url=kalshi.web_url(m["event_ticker"], series.get("title", "")),
        ),
        "event": {
            "title": ((event.result() or {}).get("event") or {}).get("title", ""),
            "markets": [kalshi.market_summary(s, category) for s in siblings],
        },
        "orderbook": book.result(),
        "history": history.result(),
        "span": span,
    }


def model_info():
    metrics = meta["test_metrics"]
    info = {k: meta[k] for k in ("window", "horizon_s", "threshold", "classes")}
    info.update(
        accuracy=metrics["model"]["accuracy"],
        baseline_accuracy=metrics["class_prior"]["accuracy"],
        linear_accuracy=metrics["linear_last_trade"]["accuracy"],
        confusion=metrics["model"]["confusion"],
        history_spans=list(kalshi.HISTORY_RANGES),
    )
    return info


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        routes = {
            "/api/info": model_info,
            "/api/history": lambda: tracker.report(
                ticker=query.get("ticker"),
                source=query.get("source"),
                since=float(query["since"]) if query.get("since") else None,
                limit=int(query["limit"]) if query.get("limit") else None,
            ),
            "/api/live": lambda: live(int(query.get("limit", 24))),
            "/api/search": lambda: {"events": kalshi.search(query.get("q", ""), 20, query.get("category"))},
            "/api/market": lambda: market_detail(query.get("ticker", ""), query.get("span", "6h")),
        }
        if url.path in routes:
            self.answer(routes[url.path])
        elif url.path == "/api/history.csv":
            entries = tracker.report(source=query.get("source"),
                                     since=float(query["since"]) if query.get("since") else None)["entries"]
            self.send_csv(tracker.to_csv(entries), "kalshi-predictions.csv")
        elif url.path == "/" or url.path.startswith("/static/"):
            self.static(url.path)
        else:
            self.reply_json({"error": "not found"}, 404)

    def do_POST(self):
        url = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self.reply_json({"error": "The request body must be JSON."}, 400)
        routes = {
            "/api/predict": lambda: predict(body.get("target"), body.get("contracts", 10)),
            "/api/scan": lambda: run_scan(
                body.get("markets", 20), body.get("contracts", 10), bool(body.get("record", True))
            ),
        }
        if url.path in routes:
            self.answer(routes[url.path])
        else:
            self.reply_json({"error": "not found"}, 404)

    def answer(self, handler):
        """Run an API handler and reply with its result, or with what went wrong."""
        try:
            body, status = handler(), 200
        except BadRequest as err:
            body, status = {"error": str(err)}, err.status
        except requests.ConnectionError:
            body, status = {"error": "Could not reach Kalshi. Check your internet connection."}, 502
        except (requests.RequestException, RuntimeError) as err:
            body, status = {"error": f"Kalshi request failed: {err}"}, 502
        except Exception as err:  # keep the server up and tell the page what broke
            body, status = {"error": f"Something went wrong: {err}"}, 500
        self.reply_json(body, status)

    def static(self, path):
        name = "index.html" if path == "/" else path.removeprefix("/static/")
        file = (STATIC / name).resolve()
        if not file.is_relative_to(STATIC.resolve()) or not file.is_file():
            return self.reply_json({"error": "not found"}, 404)
        content_type = CONTENT_TYPES.get(file.suffix, "application/octet-stream")
        self.reply(200, content_type, file.read_bytes())

    def send_csv(self, text, filename):
        data = text.encode("utf-8-sig")  # the BOM makes Excel read it as UTF-8
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def reply_json(self, body, status):
        self.reply(status, "application/json", json.dumps(body).encode())

    def reply(self, status, content_type, data):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class Server(ThreadingHTTPServer):
    # On Windows, address reuse would let us silently share a port that is already taken.
    allow_reuse_address = False
    daemon_threads = True


def main():
    global meta, model
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = ap.parse_args()

    server = None
    for port in PORTS:
        try:
            server = Server((HOST, port), Handler)
            break
        except OSError:
            continue
    if server is None:
        raise SystemExit(f"Ports {PORTS.start}-{PORTS.stop - 1} are all in use.")

    print("Loading model...")
    meta, model = load()
    # Checks each prediction once its horizon has passed and closes its paper trade.
    threading.Thread(target=tracker.run_forever, daemon=True).start()
    url = f"http://{HOST}:{server.server_address[1]}"
    print(f"Dashboard running at {url}  (press Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
