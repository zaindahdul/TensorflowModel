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


def find_markets(target):
    """Ticker named by a ticker or kalshi.com link, and its markets (None if Kalshi has none).

    The ticker may point at a whole event, in which case every market in it is returned.
    """
    ticker = target.split("?")[0].rstrip("/").split("/")[-1].upper()
    found = get_or_none(f"/markets/{ticker}")
    if found:
        return ticker, [found["market"]]
    found = get_or_none(f"/events/{ticker}")
    if found and found.get("markets"):
        return ticker, found["markets"]
    return ticker, None


def resolve_markets(target):
    """Markets named by a ticker or kalshi.com link, which may point at a whole event."""
    ticker, markets = find_markets(target)
    if markets:
        return markets
    print(
        f"Kalshi has no market or event called {ticker}. "
        "Copy the link of the market's page on kalshi.com and pass that."
    )
    raise SystemExit(1)


def trades_frame(rows):
    """Trades as returned by the API -> the DataFrame layout dataset.py expects."""
    return pd.DataFrame(
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


def analyze_market(model, meta, market):
    """Prediction for one market as a dict; "problem" is set instead when there is none."""
    ticker = market["ticker"]
    result = {"ticker": ticker, "name": market.get("yes_sub_title") or market.get("title", "")}
    rows = fetch_trades(ticker, max_pages=1)
    if not rows:
        result["problem"] = "no trades yet, cannot predict"
        return result
    events = dataset.to_events(trades_frame(rows))
    if len(events) < meta["window"]:
        result["problem"] = f"only {len(events)} trades so far, need {meta['window']} to predict"
        return result

    close_time = pd.Timestamp(market["close_time"])
    _, price, feats = dataset.market_arrays(events, close_time)
    x = dataset.make_windows(feats, np.array([len(feats) - 1]))
    probs = model.predict(x, verbose=0)[0]

    last = events["created_time"].iloc[-1]
    result["last_trade"] = last
    result["price"] = float(price[-1])
    result["age_min"] = (pd.Timestamp.now(tz="UTC") - last).total_seconds() / 60
    result["probs"] = {label: float(p) for label, p in zip(meta["classes"], probs)}
    return result


def predict_market(model, meta, market):
    result = analyze_market(model, meta, market)
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
    ap.add_argument("target", help="market ticker, event ticker, or kalshi.com link")
    args = ap.parse_args()

    markets = resolve_markets(args.target)
    meta = json.loads((ARTIFACTS / "meta.json").read_text())
    model = keras.models.load_model(ARTIFACTS / "model.keras")
    for market in markets:
        predict_market(model, meta, market)


if __name__ == "__main__":
    main()
