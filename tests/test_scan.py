from kalshi_predictor import kalshi, scan


def test_scan_ranks_by_directional_confidence(monkeypatch):
    activity = {t: {"trades": n, "yes_taker_share": 0.5, "last_trade": "x"} for t, n in [("A-1", 9), ("B-1", 5), ("C-1", 2)]}
    raw = [{"ticker": t, "event_ticker": t.split("-")[0], "title": t} for t in activity]
    probs = {
        "A-1": {"down": 0.2, "flat": 0.7, "up": 0.1},
        "B-1": {"down": 0.1, "flat": 0.1, "up": 0.8},
    }
    monkeypatch.setattr(kalshi, "tape_activity", lambda pages: (activity, 12.0))
    monkeypatch.setattr(kalshi, "get_markets", lambda tickers: [m for m in raw if m["ticker"] in tickers])
    monkeypatch.setattr(kalshi, "categories", lambda markets: {})
    monkeypatch.setattr(
        scan, "analyze_markets",
        lambda model, meta, markets: [
            {"ticker": m["ticker"], "probs": probs[m["ticker"]]} if m["ticker"] in probs
            else {"ticker": m["ticker"], "problem": "only 3 trades so far"}
            for m in markets
        ],
    )
    rows, ordered_raw, span_s = scan.scan(model=None, meta={}, markets=3)
    assert span_s == 12.0
    assert [r["ticker"] for r in rows] == ["B-1", "A-1", "C-1"]
    assert [m["ticker"] for m in ordered_raw] == ["B-1", "A-1", "C-1"]
    assert [(r["signal"], r["confidence"]) for r in rows] == [("up", 0.8), ("down", 0.2), (None, -1.0)]
    assert rows[0]["trades"] == 5
