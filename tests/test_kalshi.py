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
