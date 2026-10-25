"""Predict the short-term price direction of a live Kalshi market.

Accepts a market ticker, an event ticker (predicts every market in the event),
a series ticker (predicts the open event that closes next), or a kalshi.com link:

    python -m kalshi_predictor.predict KXBTC15M-26OCT092030-30
    python -m kalshi_predictor.predict KXBTC15M
    python -m kalshi_predictor.predict https://kalshi.com/markets/kxncaafgame/college-football-game/kxncaafgame-26oct10vtcal
"""
import argparse
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
from tensorflow import keras

from . import dataset
from .kalshi import fetch_trades, find_markets, trades_frame
from .paths import ARTIFACTS

model_lock = threading.Lock()  # Keras models are not safe to call from several threads


def resolve_markets(target):
    """Markets named by a ticker or kalshi.com link; exits if there are none."""
    ticker, markets = find_markets(target)
    if markets:
        return markets
    print(
        f"Kalshi has no market, event or series called {ticker}. "
        "Copy the link of the market's page on kalshi.com and pass that."
    )
    raise SystemExit(1)


def prepare(meta, market):
    """Fetch a market's latest trades and build the model's input from them.

    Returns (result, x): result describes the market and its last trade, and
    x is None, with result["problem"] saying why, when it cannot be predicted.
    """
    ticker = market["ticker"]
    result = {"ticker": ticker, "name": market.get("yes_sub_title") or market.get("title", "")}
    rows = fetch_trades(ticker, max_pages=1)
    if not rows:
        result["problem"] = "no trades yet, cannot predict"
        return result, None
    events = dataset.to_events(trades_frame(rows))
    if len(events) < meta["window"]:
        result["problem"] = f"only {len(events)} trades so far, need {meta['window']} to predict"
        return result, None

    close_time = pd.Timestamp(market["close_time"])
    _, price, feats = dataset.market_arrays(events, close_time)
    last = events["created_time"].iloc[-1]
    result["last_trade"] = last
    result["price"] = float(price[-1])
    result["age_min"] = (pd.Timestamp.now(tz="UTC") - last).total_seconds() / 60
    return result, dataset.make_windows(feats, np.array([len(feats) - 1]))


def run_model(model, meta, prepared):
    """Fill in result["probs"] for every (result, x) pair from prepare that has
    an input, in one model call. Returns the results."""
    ready = [(result, x) for result, x in prepared if x is not None]
    if ready:
        with model_lock:
            probs = model(np.concatenate([x for _, x in ready]), training=False).numpy()
        for (result, _), p in zip(ready, probs):
            result["probs"] = {label: float(v) for label, v in zip(meta["classes"], p)}
    return [result for result, _ in prepared]


def analyze_markets(model, meta, markets, workers=8):
    """Predictions for several markets, as dicts in the same order. Their
    trades are fetched in parallel and the model runs once for all of them;
    "problem" is set instead of "probs" for a market that cannot be predicted."""
    with ThreadPoolExecutor(workers) as pool:
        prepared = list(pool.map(lambda market: prepare(meta, market), markets))
    return run_model(model, meta, prepared)


def analyze_market(model, meta, market):
    return analyze_markets(model, meta, [market])[0]


def load():
    """The trained model's metadata and the model itself."""
    meta = json.loads((ARTIFACTS / "meta.json").read_text())
    return meta, keras.models.load_model(ARTIFACTS / "model.keras")


def print_result(meta, result):
    print(f"\n{result['ticker']}: {result['name']}")
    if "problem" in result:
        print(f"  {result['problem']}")
        return
    last, age_min = result["last_trade"], result["age_min"]
    print(f"  last trade {last:%Y-%m-%d %H:%M:%S} UTC at yes price {result['price']:.3f}")
    if age_min > 5:
        print(f"  note: that was {age_min:.0f} minutes ago, so this market is quiet right now")
    print(f"  move of >= {meta['threshold']:.2f} within {meta['horizon_s']}s of that trade:")
    for label, p in result["probs"].items():
        print(f"    {label:5s} {p:.1%}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target", help="market, event or series ticker, or a kalshi.com link")
    args = ap.parse_args()

    markets = resolve_markets(args.target)
    meta, model = load()
    for result in analyze_markets(model, meta, markets):
        print_result(meta, result)


if __name__ == "__main__":
    main()
