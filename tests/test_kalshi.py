import pytest

from kalshi_predictor import kalshi


@pytest.mark.parametrize(
    "target, ticker",
    [
        ("KXBTC15M-26OCT251900-00", "KXBTC15M-26OCT251900-00"),
        ("  kxbtc15m-26oct251900-00\n", "KXBTC15M-26OCT251900-00"),
        ("https://kalshi.com/markets/kxbtc15m/bitcoin-price-up-down/kxbtc15m-26oct251900", "KXBTC15M-26OCT251900"),
        ("https://kalshi.com/markets/kxnflgame/professional-football-game/kxnflgame-26oct25gbdet/", "KXNFLGAME-26OCT25GBDET"),
        ("kalshi.com/markets/kxbtcd/bitcoin-price-abovebelow/kxbtcd-26oct2519?utm_source=x#rules", "KXBTCD-26OCT2519"),
        ("KXBTC15M", "KXBTC15M"),
        ("", ""),
    ],
)
def test_parse_ticker(target, ticker):
    assert kalshi.parse_ticker(target) == ticker


def fake_api(responses):
    """get_or_none that answers from {path: body}; anything else is a 404."""
    def get_or_none(path, params=None):
        if path == "/events" and params:
            return responses.get(("/events", params["series_ticker"]), {"events": []})
        return responses.get(path)
    return get_or_none


def test_find_markets_prefers_a_market(monkeypatch):
    monkeypatch.setattr(kalshi, "get_or_none", fake_api({"/markets/A-1": {"market": {"ticker": "A-1"}}}))
    assert kalshi.find_markets("a-1") == ("A-1", [{"ticker": "A-1"}])


def test_find_markets_returns_every_market_in_an_event(monkeypatch):
    markets = [{"ticker": "E-1"}, {"ticker": "E-2"}]
    monkeypatch.setattr(kalshi, "get_or_none", fake_api({"/events/E": {"markets": markets}}))
    assert kalshi.find_markets("E") == ("E", markets)


def test_find_markets_picks_the_next_event_in_a_series(monkeypatch):
    later = {"event_ticker": "S-LATER", "markets": [{"ticker": "S-LATER-1", "close_time": "2026-10-26T00:00:00Z"}]}
    sooner = {"event_ticker": "S-SOONER", "markets": [{"ticker": "S-SOONER-1", "close_time": "2026-10-25T23:00:00Z"}]}
    monkeypatch.setattr(kalshi, "get_or_none", fake_api({("/events", "S"): {"events": [later, sooner]}}))
    assert kalshi.find_markets("S") == ("S-SOONER", sooner["markets"])


def test_find_markets_none_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(kalshi, "get_or_none", fake_api({}))
    assert kalshi.find_markets("NOPE") == ("NOPE", None)


def test_search_normalizes_events_and_drops_settled_markets(monkeypatch):
    body = {"current_page": [
        {
            "event_ticker": "KXNFLGAME-26OCT25GBDET", "series_ticker": "KXNFLGAME",
            "event_title": "GB Packers vs DET Lions", "event_subtitle": "GB vs DET (Oct 25)",
            "category": "Sports", "recent_volume": 19431733,
            "markets": [
                {"ticker": "KXNFLGAME-26OCT25GBDET-DET", "yes_subtitle": "DET Lions", "result": "",
                 "yes_bid_dollars": "0.6100", "yes_ask_dollars": "0.6200", "last_price_dollars": "0.6100",
                 "close_ts": "2026-10-27T20:25:00Z", "volume": 10},
                {"ticker": "KXNFLGAME-26OCT25GBDET-GB", "yes_subtitle": "GB Packers", "result": "",
                 "yes_bid_dollars": "0.3800", "yes_ask_dollars": "0.3900", "last_price_dollars": "",
                 "close_ts": "2026-10-27T20:25:00Z", "volume": 99},
                {"ticker": "KXNFLGAME-26OCT25GBDET-TIE", "yes_subtitle": "Tie", "result": "no", "volume": 1},
            ],
        },
        {"event_ticker": "KXMVE-1", "markets": [{"ticker": "KXMVE-1-A", "result": ""}]},
    ]}
    seen = {}
    def get(path, params=None):
        seen.update(path=path, params=params)
        return body
    monkeypatch.setattr(kalshi, "get", get)

    events = kalshi.search(" packers ", limit=5)
    assert seen == {"path": kalshi.SEARCH_URL, "params": {"query": "packers", "page_size": 5, "status": "open"}}
    assert len(events) == 1  # the combo event has nothing left
    event = events[0]
    assert (event["title"], event["category"], event["volume"]) == ("GB Packers vs DET Lions", "Sports", 19431733)
    assert [m["ticker"] for m in event["markets"]] == ["KXNFLGAME-26OCT25GBDET-GB", "KXNFLGAME-26OCT25GBDET-DET"]
    assert event["markets"][0] == {
        "ticker": "KXNFLGAME-26OCT25GBDET-GB", "name": "GB Packers", "yes_bid": 0.38, "yes_ask": 0.39,
        "last_price": None, "close_time": "2026-10-27T20:25:00Z", "volume": 99,
    }
