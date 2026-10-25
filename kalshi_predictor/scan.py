"""Find the live markets the model is most confident about.

Takes the markets trading the most on the exchange right now, predicts every
one of them in a single batch, and ranks them by how sure the model is that
the price moves (up or down) in the next minute.

    python -m kalshi_predictor scan
    python -m kalshi_predictor scan --markets 40 --show 15
"""
import argparse

from . import kalshi
from .predict import analyze_markets, load


def confidence(result):
    """The model's probability of its likelier direction; -1 if it has no prediction."""
    probs = result.get("probs")
    return max(probs["up"], probs["down"]) if probs else -1.0


def signal(result):
    """"up" or "down", whichever the model thinks is likelier, or None."""
    probs = result.get("probs")
    return None if not probs else "up" if probs["up"] >= probs["down"] else "down"


def scan(model, meta, markets=20, pages=4):
    """Predict the busiest live markets.

    Returns (rows, raw markets, seconds of trading the ranking covers). Each
    row is a market summary with its tape activity and a "prediction"; rows
    are ordered most confident first and raw markets match them one to one.
    """
    activity, span_s = kalshi.tape_activity(pages)
    raw = kalshi.get_markets(list(activity)[:markets])
    results = analyze_markets(model, meta, raw)
    cats = kalshi.categories(raw)
    rows = [
        dict(
            kalshi.market_summary(m, cats.get(kalshi.series_of(m), "")),
            **activity[m["ticker"]],
            prediction=result,
            signal=signal(result),
            confidence=confidence(result),
        )
        for m, result in zip(raw, results)
    ]
    order = sorted(range(len(rows)), key=lambda i: -rows[i]["confidence"])
    return [rows[i] for i in order], [raw[i] for i in order], span_s


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--markets", type=int, default=25, help="how many of the busiest markets to predict")
    ap.add_argument("--show", type=int, default=10, help="how many of the most confident to print")
    args = ap.parse_args()

    meta, model = load()
    rows, _, span_s = scan(model, meta, args.markets)
    print(f"\nThe {len(rows)} busiest markets over the last {span_s:.0f}s of trading, most confident first.")
    print(f"Chance of a move of at least {meta['threshold']:.2f} within {meta['horizon_s']}s:\n")
    print(f"{'signal':>8s} {'conf':>5s} {'flat':>5s} {'price':>6s} {'trades':>7s}  market")
    for row in rows[: args.show]:
        result = row["prediction"]
        if "probs" not in result:
            continue
        name = row["title"] if row["name"] == row["title"] else f"{row['title']} - {row['name']}"
        print(
            f"{row['signal'].upper():>8s} {row['confidence']:5.0%} {result['probs']['flat']:5.0%} "
            f"{result['price']:6.2f} {row['trades']:7d}  {row['ticker']}  {name[:60]}"
        )


if __name__ == "__main__":
    main()
