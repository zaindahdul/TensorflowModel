import numpy as np
import pandas as pd
import pytest

from kalshi_predictor import dataset

T0 = pd.Timestamp("2026-10-01 12:00", tz="UTC")


def trades(rows):
    """rows: (ticker, seconds after T0, yes_price, count, taker_side)."""
    return pd.DataFrame(
        {
            "ticker": [r[0] for r in rows],
            "created_time": [T0 + pd.Timedelta(seconds=r[1]) for r in rows],
            "yes_price": [r[2] for r in rows],
            "count": [r[3] for r in rows],
            "taker_side": [r[4] for r in rows],
        }
    )


def test_to_events_merges_fills_of_one_order():
    ev = dataset.to_events(
        trades([("A", 0, 0.40, 10, "yes"), ("A", 0, 0.42, 30, "yes"), ("A", 5, 0.41, 1, "no")])
    )
    assert len(ev) == 2
    first = ev.iloc[0]
    assert first["count"] == 40
    assert first["yes_price"] == pytest.approx((0.40 * 10 + 0.42 * 30) / 40)


def test_to_events_sorts_by_market_then_time():
    ev = dataset.to_events(trades([("B", 0, 0.5, 1, "yes"), ("A", 9, 0.5, 1, "yes"), ("A", 1, 0.5, 1, "no")]))
    assert ev["ticker"].tolist() == ["A", "A", "B"]
    assert (ev[ev["ticker"] == "A"]["created_time"].diff().dropna() > pd.Timedelta(0)).all()


def test_event_features_columns_match_feature_names():
    ts = np.array([0.0, 2.0, 5.0])
    price = np.array([0.50, 0.52, 0.51])
    side = np.array([1.0, -1.0, 1.0])
    size = np.array([1.0, 9.0, 3.0])
    feats = dataset.event_features(ts, price, side, size, close_ts=65.0)
    assert feats.shape == (3, len(dataset.FEATURES))
    col = {name: feats[:, i] for i, name in enumerate(dataset.FEATURES)}
    np.testing.assert_allclose(col["price_change"], [0, 0.02, -0.01], atol=1e-6)
    np.testing.assert_allclose(col["signed_log_size"], side * np.log1p(size), rtol=1e-6)
    np.testing.assert_allclose(col["log_seconds_since_prev"], np.log1p([0, 2, 3]), rtol=1e-6)
    np.testing.assert_allclose(col["log_seconds_to_close"], np.log1p([65, 63, 60]), rtol=1e-6)


def test_make_windows_is_relative_to_the_latest_price():
    n = dataset.WINDOW + 5
    price = np.linspace(0.30, 0.60, n)
    feats = dataset.event_features(np.arange(n, dtype=float), price, np.ones(n), np.ones(n), n + 100.0)
    x = dataset.make_windows(feats, np.array([dataset.WINDOW - 1, n - 1]))
    assert x.shape == (2, dataset.WINDOW, len(dataset.FEATURES))
    rel = dataset.FEATURES.index("price_vs_latest")
    assert x[0, -1, rel] == 0
    np.testing.assert_allclose(x[1, :, rel], price[-dataset.WINDOW:] - price[-1], atol=1e-6)


def test_build_samples_labels_the_move_over_the_horizon(tmp_path, monkeypatch):
    # Flat at 50c, then a jump to 55c: anchors more than one horizon before the
    # jump are flat, anchors within one horizon of it see the move up.
    n = dataset.WINDOW + 40
    rows = [("A", i * 10, 0.50 if i < n - 3 else 0.55, 1, "yes") for i in range(n)]
    trades(rows).to_parquet(tmp_path / "trades.parquet")
    pd.DataFrame(
        {
            "ticker": ["A"],
            "close_time": [T0 + pd.Timedelta(days=1)],
            "fetched_at": [T0 + pd.Timedelta(hours=1)],
        }
    ).to_parquet(tmp_path / "markets.parquet")
    monkeypatch.setattr(dataset, "DATA_DIR", tmp_path)

    x, y, ts, tickers = dataset.build_samples(horizon_s=60, threshold=0.01)
    assert len(x) == len(y) == len(ts) == len(tickers)
    assert set(tickers) == {"A"}
    assert (y == dataset.CLASSES.index("up")).sum() == 6  # anchors at 810s..860s
    jump_ts = (n - 3) * 10
    for anchor_ts, label in zip(ts - T0.timestamp(), y):
        if anchor_ts >= jump_ts:
            assert label == dataset.CLASSES.index("flat")  # already at 55c, nothing after
        elif anchor_ts + 60 >= jump_ts:
            assert label == dataset.CLASSES.index("up")
        else:
            assert label == dataset.CLASSES.index("flat")
