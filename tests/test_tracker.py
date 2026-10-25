import pandas as pd
import pytest

from kalshi_predictor import tracker

META = {"horizon_s": 60, "threshold": 0.01}
ANCHOR = pd.Timestamp("2026-10-01 12:00", tz="UTC")
real_fee_multiplier = tracker.fee_multiplier  # before the fixture stubs it out


@pytest.fixture(autouse=True)
def isolated_history(tmp_path, monkeypatch):
    """Never read or write the real prediction history."""
    monkeypatch.setattr(tracker, "HISTORY", tmp_path / "history.json")
    monkeypatch.setattr(tracker, "history", [])
    monkeypatch.setattr(tracker, "fee_multiplier", lambda event_ticker: 1.0)


def api_trade(seconds_after_anchor, yes_price, ticker="A-1"):
    created = ANCHOR + pd.Timedelta(seconds=seconds_after_anchor)
    return {
        "ticker": ticker,
        "created_time": created.isoformat().replace("+00:00", "Z"),
        "yes_price_dollars": f"{yes_price:.4f}",
        "count_fp": "1.00",
        "taker_side": "yes",
    }


def result(probs):
    return {"ticker": "A-1", "name": "A", "last_trade": ANCHOR, "price": 0.50, "probs": probs}


def market(**quotes):
    return {"ticker": "A-1", "event_ticker": "A", "title": "Will A happen?", **quotes}


@pytest.mark.parametrize(
    "contracts, price, fee",
    [(10, 0.50, 0.18), (100, 0.01, 0.07), (1, 0.50, 0.02), (1, 0.99, 0.01), (100, 0.50, 1.75)],
)
def test_taker_fee_rounds_up_to_the_cent(contracts, price, fee):
    assert tracker.taker_fee(contracts, price, 1.0) == fee


def test_up_prediction_buys_yes_at_the_ask():
    entry = tracker.record(
        META, market(yes_ask_dollars="0.5200"), result({"down": 0.1, "flat": 0.2, "up": 0.7}), 10, 1000.0
    )
    assert entry["predicted"] == "up"
    assert entry["due"] == 1060.0
    assert entry["trade"] == {
        "side": "yes", "contracts": 10, "entry_price": 0.52, "entry_fee": 0.18, "fee_multiplier": 1.0,
    }
    assert tracker.HISTORY.exists()


def test_down_prediction_buys_no():
    entry = tracker.record(
        META, market(no_ask_dollars="0.4900"), result({"down": 0.6, "flat": 0.2, "up": 0.2}), 5, 0.0
    )
    assert entry["trade"]["side"] == "no"
    assert entry["trade"]["entry_price"] == 0.49


def test_flat_prediction_does_not_trade():
    entry = tracker.record(META, market(), result({"down": 0.1, "flat": 0.8, "up": 0.1}), 10, 0.0)
    assert entry["trade"]["side"] is None
    assert "flat" in entry["trade"]["skipped"]


def test_no_ask_means_no_trade():
    entry = tracker.record(META, market(yes_ask_dollars="0"), result({"down": 0, "flat": 0, "up": 1}), 10, 0.0)
    assert "nobody was selling" in entry["trade"]["skipped"]


def test_check_outcome_uses_the_last_trade_inside_the_horizon(monkeypatch):
    rows = [api_trade(10, 0.51), api_trade(50, 0.53), api_trade(61, 0.40)]
    monkeypatch.setattr(tracker, "fetch_trades", lambda *args, **kwargs: rows)
    entry = {
        "ticker": "A-1", "anchor_time": ANCHOR.isoformat(), "anchor_price": 0.50,
        "horizon_s": 60, "threshold": 0.01, "predicted": "up",
    }
    assert tracker.check_outcome(entry) == {"actual": "up", "price": 0.53, "correct": True}


def test_check_outcome_is_flat_without_later_trades(monkeypatch):
    monkeypatch.setattr(tracker, "fetch_trades", lambda *args, **kwargs: [])
    entry = {
        "ticker": "A-1", "anchor_time": ANCHOR.isoformat(), "anchor_price": 0.50,
        "horizon_s": 60, "threshold": 0.01, "predicted": "down",
    }
    assert tracker.check_outcome(entry) == {"actual": "flat", "price": 0.50, "correct": False}


def test_close_trade_sells_at_the_bid_after_fees(monkeypatch):
    monkeypatch.setattr(tracker, "get", lambda path: {"market": market(yes_bid_dollars="0.6000", result="")})
    trade = {"side": "yes", "contracts": 10, "entry_price": 0.52, "entry_fee": 0.18, "fee_multiplier": 1.0}
    tracker.close_trade(trade, "A-1", late=False)
    assert trade["exit_price"] == 0.60
    assert trade["exit_fee"] == 0.17
    assert trade["pnl"] == pytest.approx(10 * 0.08 - 0.18 - 0.17)


def test_close_trade_pays_out_a_resolved_market(monkeypatch):
    monkeypatch.setattr(tracker, "get", lambda path: {"market": market(result="no")})
    trade = {"side": "no", "contracts": 10, "entry_price": 0.40, "entry_fee": 0.17, "fee_multiplier": 1.0}
    tracker.close_trade(trade, "A-1", late=False)
    assert trade["exit_price"] == 1.0
    assert trade["pnl"] == pytest.approx(6.0 - 0.17)


def test_late_trades_are_not_sold():
    trade = {"side": "yes", "contracts": 1, "entry_price": 0.5, "entry_fee": 0.02, "fee_multiplier": 1.0}
    tracker.close_trade(trade, "A-1", late=True)
    assert "pnl" not in trade
    assert "closed" in trade["problem"]


def test_report_totals():
    tracker.history.extend([
        {"status": "done", "outcome": {"correct": True}, "trade": {"pnl": 1.5, "entry_fee": 0.1, "exit_fee": 0.1}},
        {"status": "done", "outcome": {"correct": False}, "trade": {"pnl": -0.5, "entry_fee": 0.1, "exit_fee": 0.0}},
        {"status": "pending", "trade": {}},
    ])
    s = tracker.report()["summary"]
    assert (s["predictions"], s["pending"], s["checked"], s["correct"]) == (3, 1, 2, 1)
    assert (s["trades"], s["winning_trades"], s["pnl"], s["fees"]) == (2, 1, 1.0, 0.3)


def test_fee_multiplier_comes_from_the_series(monkeypatch):
    monkeypatch.setattr(tracker, "series_info", lambda name: {"KXA": {"fee_multiplier": 0.5}, "KXB": {}}[name])
    assert real_fee_multiplier("KXA-26OCT") == 0.5
    assert real_fee_multiplier("KXB-26OCT") == 1.0
