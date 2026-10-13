"""Turn raw Kalshi trades into (window of recent trades) -> (future price move) samples."""
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).parent / "data"

WINDOW = 50  # trade events per sample
HORIZON_S = 60  # how far ahead the label looks, in seconds
THRESHOLD = 0.01  # a move of at least one cent counts as up/down
MAX_SAMPLES_PER_MARKET = 500  # keeps the busiest markets from dominating

FEATURES = [
    "yes_price",
    "price_vs_latest",  # price minus the latest price in the window
    "price_change",  # price minus previous event's price
    "taker_side",  # +1 taker bought yes, -1 taker bought no
    "log_size",
    "signed_log_size",
    "log_seconds_since_prev",
    "log_seconds_to_close",
]
CLASSES = ["down", "flat", "up"]


def to_events(trades):
    """Merge fills from one taker order (same market, timestamp and side) into one event."""
    trades = trades.assign(notional=trades["yes_price"] * trades["count"])
    events = trades.groupby(["ticker", "created_time", "taker_side"], as_index=False).agg(
        count=("count", "sum"), notional=("notional", "sum")
    )
    events["yes_price"] = events["notional"] / events["count"]
    return events.sort_values(["ticker", "created_time"], kind="stable").reset_index(drop=True)


def event_features(ts, price, side, size, close_ts):
    """Per-event feature rows for one market. All inputs are time-ordered arrays.

    price_vs_latest depends on the window, so it is left as zeros here and filled
    in by make_windows.
    """
    log_size = np.log1p(size)
    return np.stack(
        [
            price,
            np.zeros_like(price),
            np.diff(price, prepend=price[0]),
            side,
            log_size,
            side * log_size,
            np.log1p(np.diff(ts, prepend=ts[0])),
            np.log1p(np.clip(close_ts - ts, 0, None)),
        ],
        axis=1,
    ).astype(np.float32)


def make_windows(feats, anchors):
    """Windows of WINDOW events ending at (and including) each anchor index."""
    idx = anchors[:, None] + np.arange(-WINDOW + 1, 1)[None, :]
    x = feats[idx]
    x[:, :, 1] = x[:, :, 0] - x[:, -1:, 0]
    return x


def market_arrays(events, close_time):
    # as_unit matters: parquet round-trips timestamps as microseconds, not nanoseconds.
    ts = events["created_time"].dt.as_unit("ns").astype("int64").to_numpy() / 1e9
    price = events["yes_price"].to_numpy(dtype=np.float64)
    side = np.where(events["taker_side"].to_numpy() == "yes", 1.0, -1.0)
    size = events["count"].to_numpy(dtype=np.float64)
    return ts, price, event_features(ts, price, side, size, close_time.timestamp())


def build_samples(horizon_s=HORIZON_S, threshold=THRESHOLD):
    """Returns x (n, WINDOW, features), y (n,) in {0 down, 1 flat, 2 up}, anchor
    times (n,) and the ticker each sample came from (n,)."""
    trades = pd.read_parquet(DATA_DIR / "trades.parquet")
    markets = pd.read_parquet(DATA_DIR / "markets.parquet").set_index("ticker")
    xs, ys, ts_out, tickers = [], [], [], []
    for ticker, ev in to_events(trades).groupby("ticker", sort=False):
        meta = markets.loc[ticker]
        ts, price, feats = market_arrays(ev, meta["close_time"])
        # The label is only known if the whole horizon was observed: it must end
        # before we downloaded the trades and before the market stopped trading.
        cutoff = min(meta["fetched_at"], meta["close_time"]).timestamp()
        anchors = np.arange(WINDOW - 1, len(ts))
        anchors = anchors[ts[anchors] + horizon_s <= cutoff]
        if len(anchors) == 0:
            continue
        if len(anchors) > MAX_SAMPLES_PER_MARKET:
            keep = np.linspace(0, len(anchors) - 1, MAX_SAMPLES_PER_MARKET).round().astype(int)
            anchors = anchors[keep]
        # Future price = last traded price at or before anchor time + horizon.
        future = np.searchsorted(ts, ts[anchors] + horizon_s, side="right") - 1
        move = price[future] - price[anchors]
        y = np.ones(len(anchors), dtype=np.int64)
        y[move <= -threshold + 1e-9] = 0
        y[move >= threshold - 1e-9] = 2
        xs.append(make_windows(feats, anchors))
        ys.append(y)
        ts_out.append(ts[anchors])
        tickers.append(np.full(len(anchors), ticker))
    return tuple(np.concatenate(a) for a in (xs, ys, ts_out, tickers))


def chronological_split(x, y, ts, tickers, horizon_s=HORIZON_S, train=0.70, val=0.15):
    """Split so that evaluation is on markets, and times, the model never trained on.

    Markets are ordered by their median sample time and assigned whole to train,
    val or test. Samples are then also cut at the time boundaries between splits,
    with a gap of one horizon so no training label can see into a later split.
    """
    names = np.unique(tickers)
    median_ts = np.array([np.median(ts[tickers == n]) for n in names])
    order = np.argsort(median_ts)
    i_val, i_test = int(len(names) * train), int(len(names) * (train + val))
    t_val, t_test = median_ts[order[i_val]], median_ts[order[i_test]]
    in_split = {
        "train": np.isin(tickers, names[order[:i_val]]) & (ts <= t_val - horizon_s),
        "val": np.isin(tickers, names[order[i_val:i_test]])
        & (ts > t_val)
        & (ts <= t_test - horizon_s),
        "test": np.isin(tickers, names[order[i_test:]]) & (ts > t_test),
    }
    for name, mask in in_split.items():
        if not mask.any():
            raise ValueError(f"{name} split is empty; fetch more markets")
    return {name: (x[m], y[m]) for name, m in in_split.items()}
