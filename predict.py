"""Predict the short-term price direction of a live Kalshi market.

Accepts a market ticker, an event ticker (predicts every market in the event),
or a kalshi.com link / the end of one:

    python predict.py KXBTC15M-26OCT092030-30
    python predict.py https://kalshi.com/markets/kxncaafgame/college-football-game/kxncaafgame-26oct10vtcal
"""
import argparse
import json

import numpy as np
import pandas as pd
import requests
from tensorflow import keras

import dataset
from fetch_data import fetch_trades, get
from train import ARTIFACTS


def get_or_none(path):
    try:
        return get(path)
    except requests.HTTPError as err:
        if err.response.status_code == 404:
            return None
        raise


def resolve_markets(target):
    """Markets named by a ticker or kalshi.com link, which may point at a whole event."""
    ticker = target.split("?")[0].rstrip("/").split("/")[-1].upper()
    found = get_or_none(f"/markets/{ticker}")
    if found:
        return [found["market"]]
    found = get_or_none(f"/events/{ticker}")
    if found and found.get("markets"):
        return found["markets"]
    print(
        f"Kalshi has no market or event called {ticker}. "
        "Copy the link of the market's page on kalshi.com and pass that."
    )
    raise SystemExit(1)


def predict_market(model, meta, market):
    ticker = market["ticker"]
    name = market.get("yes_sub_title") or market.get("title", "")
    print(f"\n{ticker}: {name}")
    rows = fetch_trades(ticker, max_pages=1)
    if not rows:
        print("  no trades yet, cannot predict")
        return
    trades = pd.DataFrame(
        {
            "ticker": [r["ticker"] for r in rows],
            "created_time": pd.to_datetime(
                [r["created_time"] for r in rows], format="ISO8601", utc=True
            ),
            "yes_price": [float(r["yes_price_dollars"]) for r in rows],
            "count": [float(r["count_fp"]) for r in rows],
            "taker_side": [r["taker_side"] for r in rows],
        }
    )
    events = dataset.to_events(trades)
    if len(events) < meta["window"]:
        print(f"  only {len(events)} trades so far, need {meta['window']} to predict")
        return

    close_time = pd.Timestamp(market["close_time"])
    _, price, feats = dataset.market_arrays(events, close_time)
    x = dataset.make_windows(feats, np.array([len(feats) - 1]))
    probs = model.predict(x, verbose=0)[0]

    last = events["created_time"].iloc[-1]
    age_min = (pd.Timestamp.now(tz="UTC") - last).total_seconds() / 60
    print(f"  last trade {last:%Y-%m-%d %H:%M:%S} UTC at yes price {price[-1]:.3f}")
    if age_min > 5:
        print(f"  note: that was {age_min:.0f} minutes ago, so this market is quiet right now")
    print(f"  move of >= {meta['threshold']:.2f} within {meta['horizon_s']}s of that trade:")
    for label, p in zip(meta["classes"], probs):
        print(f"    {label:5s} {p:.1%}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target", help="market ticker, event ticker, or kalshi.com link")
    args = ap.parse_args()

    markets = resolve_markets(args.target)
    meta = json.loads((ARTIFACTS / "meta.json").read_text())
    model = keras.models.load_model(ARTIFACTS / "model.keras")
    for market in markets:
        predict_market(model, meta, market)


if __name__ == "__main__":
    main()
