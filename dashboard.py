"""Local web dashboard for predict.py: paste a Kalshi market, see the prediction.

    python dashboard.py

Opens http://127.0.0.1:8765 in your browser. Only this computer can reach it.
"""
import argparse
import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

import requests
from tensorflow import keras

from predict import analyze_market, find_markets
from train import ARTIFACTS

PAGE = Path(__file__).parent / "dashboard.html"
HOST = "127.0.0.1"
PORTS = range(8765, 8775)

meta = json.loads((ARTIFACTS / "meta.json").read_text())
model = None
model_lock = threading.Lock()  # one prediction at a time; Keras models are not thread-safe


def predict(target):
    """Response body and HTTP status for one lookup."""
    target = target.strip()
    if not target:
        return {"error": "Paste a Kalshi market link or ticker first."}, 400
    try:
        ticker, markets = find_markets(target)
        if not markets:
            return {
                "error": f"Kalshi has no market or event called {ticker}. "
                "Copy the link of the market's page on kalshi.com and paste that."
            }, 404
        results = []
        for market in markets:
            with model_lock:
                result = analyze_market(model, meta, market)
            result["title"] = market.get("title", "")
            if "last_trade" in result:
                result["last_trade"] = result["last_trade"].isoformat()
            results.append(result)
    except requests.ConnectionError:
        return {"error": "Could not reach Kalshi. Check your internet connection."}, 502
    except (requests.RequestException, RuntimeError) as err:
        return {"error": f"Kalshi request failed: {err}"}, 502
    return {"results": results}, 200


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            self.reply(200, "text/html; charset=utf-8", PAGE.read_bytes())
        elif url.path == "/api/info":
            info = {k: meta[k] for k in ("window", "horizon_s", "threshold", "classes")}
            info["accuracy"] = meta["test_metrics"]["model"]["accuracy"]
            info["baseline_accuracy"] = meta["test_metrics"]["class_prior"]["accuracy"]
            self.reply_json(info, 200)
        elif url.path == "/api/predict":
            target = parse_qs(url.query).get("target", [""])[0]
            try:
                body, status = predict(target)
            except Exception as err:  # keep the server up and tell the page what broke
                body, status = {"error": f"Prediction failed: {err}"}, 500
            self.reply_json(body, status)
        else:
            self.reply_json({"error": "not found"}, 404)

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
    global model
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
    model = keras.models.load_model(ARTIFACTS / "model.keras")
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
