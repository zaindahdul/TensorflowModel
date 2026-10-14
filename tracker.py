"""Paper-trade every dashboard prediction, then check whether it came true and
what acting on it would have made.

The trade: a prediction of "up" buys Yes and "down" buys No, at the ask when the
prediction is made; "flat" does not trade. The position is sold at the bid
horizon_s seconds later (or settles at $1 / $0 if the market resolves first).
Fees follow Kalshi's taker formula, 0.07 x fee multiplier x C x P x (1 - P),
rounded up to the cent. It assumes the whole order fills at the best price.

"Correct" uses the model's own definition: which way the last traded price moved
in the horizon_s seconds after the last trade the model saw.
"""
import copy
import json
import math
import os
import threading
import time
import traceback
import uuid
from pathlib import Path

import pandas as pd
import requests

import dataset
from fetch_data import fetch_trades, get
from predict import trades_frame

HISTORY = Path(__file__).parent / "data" / "prediction_history.json"
GRACE_S = 2  # lets trades from the end of the horizon reach the API
LATE_S = 30  # past this, the bid we would see is not the one you could have sold at
GIVE_UP_S = 600  # stop retrying a check that keeps failing

lock = threading.Lock()  # guards history
history = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
fee_multipliers = {}


def save():
    """Write history to disk. Call with lock held."""
    HISTORY.parent.mkdir(exist_ok=True)
    tmp = HISTORY.with_suffix(".tmp")
    tmp.write_text(json.dumps(history, indent=1))
    os.replace(tmp, HISTORY)


def fee_multiplier(event_ticker):
    series = event_ticker.split("-")[0]
    if series not in fee_multipliers:
        try:
            found = get(f"/series/{series}")["series"].get("fee_multiplier")
            fee_multipliers[series] = float(found if found is not None else 1)
        except (requests.RequestException, RuntimeError, KeyError):
            return 1.0  # not cached, so the next trade tries again
    return fee_multipliers[series]


def taker_fee(contracts, price, multiplier):
    fee = 0.07 * multiplier * contracts * price * (1 - price)
    return math.ceil(round(fee * 100, 6)) / 100


def quote(market, side, which):
    """Best yes/no bid or ask in dollars; 0 when there is none."""
    return float(market.get(f"{side}_{which}_dollars") or 0)


def record(meta, market, result, contracts, quoted_at):
    """Log one prediction from analyze_market and open its paper trade.

    market is the market dict (with its bid and ask) fetched at quoted_at.
    Returns the stored entry.
    """
    predicted = max(result["probs"], key=result["probs"].get)
    side = {"up": "yes", "down": "no"}.get(predicted)
    trade = {"side": side, "contracts": contracts}
    if side is None:
        trade["skipped"] = "the model predicted flat, so there was nothing to trade"
    else:
        ask = quote(market, side, "ask")
        if not 0 < ask < 1:
            trade["skipped"] = f"nobody was selling {side.capitalize()} contracts"
        else:
            multiplier = fee_multiplier(market["event_ticker"])
            trade.update(
                entry_price=ask,
                entry_fee=taker_fee(contracts, ask, multiplier),
                fee_multiplier=multiplier,
            )
    entry = {
        "id": uuid.uuid4().hex,
        "created": quoted_at,
        "due": quoted_at + meta["horizon_s"],
        "status": "pending",
        "ticker": result["ticker"],
        "title": market.get("title", ""),
        "name": result["name"],
        "horizon_s": meta["horizon_s"],
        "threshold": meta["threshold"],
        "anchor_time": result["last_trade"].isoformat(),
        "anchor_price": result["price"],
        "probs": result["probs"],
        "predicted": predicted,
        "trade": trade,
    }
    with lock:
        history.append(entry)
        save()
    return entry


def check_outcome(entry):
    """Which way the last traded price moved in the horizon after the anchor trade."""
    start = pd.Timestamp(entry["anchor_time"])
    end = start + pd.Timedelta(seconds=entry["horizon_s"])
    rows = fetch_trades(
        entry["ticker"],
        max_pages=50,
        min_ts=math.floor(start.timestamp()),
        max_ts=math.ceil(end.timestamp()),
    )
    future = entry["anchor_price"]
    if rows:
        events = dataset.to_events(trades_frame(rows))
        later = events[(events["created_time"] > start) & (events["created_time"] <= end)]
        if len(later):
            future = float(later["yes_price"].iloc[-1])
    move = future - entry["anchor_price"]
    actual = "flat"
    if move <= -entry["threshold"] + 1e-9:
        actual = "down"
    elif move >= entry["threshold"] - 1e-9:
        actual = "up"
    return {"actual": actual, "price": future, "correct": actual == entry["predicted"]}


def close_trade(trade, ticker, late):
    """Sell the paper position, filling in exit_price, exit_fee and pnl."""
    if "entry_price" not in trade or "pnl" in trade or "problem" in trade:
        return
    if late:
        trade["problem"] = "the dashboard was closed when this trade was due to be sold"
        return
    market = get(f"/markets/{ticker}")["market"]
    contracts, side = trade["contracts"], trade["side"]
    if market.get("result") in ("yes", "no"):
        price, fee = (1.0 if market["result"] == side else 0.0), 0.0
        trade["note"] = f"the market resolved {market['result'].capitalize()} before the sale"
    else:
        price = quote(market, side, "bid")
        fee = taker_fee(contracts, price, trade["fee_multiplier"])
        if price <= 0:
            trade["note"] = "nobody was buying, so the position is valued at $0"
    gross = contracts * (price - trade["entry_price"])
    trade.update(
        exit_price=price,
        exit_fee=fee,
        pnl=round(gross - trade["entry_fee"] - fee, 2),
    )


def resolve(entry, now):
    """Updated copy of a pending entry, or None to try again later."""
    entry = copy.deepcopy(entry)
    try:
        close_trade(entry["trade"], entry["ticker"], late=now - entry["due"] > LATE_S)
        entry["outcome"] = check_outcome(entry)
    except (requests.RequestException, RuntimeError, KeyError) as err:
        if now - entry["due"] < GIVE_UP_S:
            # Keep a sale that already went through; the outcome check is retried.
            return entry if "pnl" in entry["trade"] else None
        entry["error"] = f"could not check this prediction: {err}"
    entry["status"] = "done"
    return entry


def resolve_due():
    now = time.time()
    with lock:
        due = [e for e in history if e["status"] == "pending" and e["due"] + GRACE_S <= now]
    for entry in due:
        updated = resolve(entry, now)
        if updated is None:
            continue
        with lock:
            for i, e in enumerate(history):
                if e["id"] == updated["id"]:
                    history[i] = updated
            save()


def run_forever(interval_s=1.0):
    while True:
        try:
            resolve_due()
        except Exception:  # keep checking later predictions even if one check breaks
            traceback.print_exc()
        time.sleep(interval_s)


def report():
    """History (newest first) and running totals, ready to send as JSON."""
    with lock:
        entries = copy.deepcopy(history)
    checked = [e for e in entries if "outcome" in e]
    trades = [e["trade"] for e in entries if "pnl" in e["trade"]]
    return {
        "entries": entries[::-1],
        "summary": {
            "predictions": len(entries),
            "pending": sum(e["status"] == "pending" for e in entries),
            "checked": len(checked),
            "correct": sum(e["outcome"]["correct"] for e in checked),
            "trades": len(trades),
            "winning_trades": sum(t["pnl"] > 0 for t in trades),
            "pnl": round(sum(t["pnl"] for t in trades), 2),
            "fees": round(sum(t["entry_fee"] + t["exit_fee"] for t in trades), 2),
        },
    }
