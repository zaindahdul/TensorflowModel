"""Train a model that predicts the short-term direction of a Kalshi market's price.

    python -m kalshi_predictor.train
"""
import argparse
import json

import numpy as np
from tensorflow import keras

from . import dataset
from .paths import ARTIFACTS


def build_model(train_x):
    norm = keras.layers.Normalization()
    norm.adapt(train_x)  # statistics from the training split only
    inputs = keras.Input(shape=train_x.shape[1:])
    h = norm(inputs)
    h = keras.layers.Conv1D(32, 3, padding="same", activation="relu")(h)
    h = keras.layers.GRU(64)(h)
    h = keras.layers.Dropout(0.2)(h)
    h = keras.layers.Dense(32, activation="relu")(h)
    outputs = keras.layers.Dense(len(dataset.CLASSES), activation="softmax")(h)
    return keras.Model(inputs, outputs)


def build_linear_baseline(train_x):
    """Multinomial logistic regression on the most recent trade only."""
    norm = keras.layers.Normalization()
    norm.adapt(train_x[:, -1, :])
    inputs = keras.Input(shape=train_x.shape[1:])
    outputs = keras.layers.Dense(len(dataset.CLASSES), activation="softmax")(norm(inputs[:, -1, :]))
    return keras.Model(inputs, outputs)


def fit(model, splits, epochs, lr):
    model.compile(
        optimizer=keras.optimizers.Adam(lr),
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )
    model.fit(
        *splits["train"],
        validation_data=splits["val"],
        epochs=epochs,
        batch_size=256,
        callbacks=[keras.callbacks.EarlyStopping(patience=3, restore_best_weights=True)],
        verbose=2,
    )
    return model


def score(probs, y):
    n_classes = probs.shape[1]
    pred = probs.argmax(axis=1)
    confusion = np.zeros((n_classes, n_classes), dtype=int)
    np.add.at(confusion, (y, pred), 1)
    tp = np.diag(confusion)
    f1 = 2 * tp / np.maximum(confusion.sum(axis=0) + confusion.sum(axis=1), 1)
    return {
        "log_loss": float(-np.log(np.clip(probs[np.arange(len(y)), y], 1e-7, 1)).mean()),
        "accuracy": float((pred == y).mean()),
        "macro_f1": float(f1.mean()),
        "confusion": confusion.tolist(),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--horizon", type=int, default=dataset.HORIZON_S, help="seconds ahead")
    ap.add_argument("--threshold", type=float, default=dataset.THRESHOLD, help="min move, dollars")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    keras.utils.set_random_seed(args.seed)

    x, y, ts, tickers = dataset.build_samples(args.horizon, args.threshold)
    splits = dataset.chronological_split(x, y, ts, tickers, args.horizon)
    for name, (sx, sy) in splits.items():
        share = np.bincount(sy, minlength=3) / len(sy)
        print(f"{name}: {len(sy):,} samples, down/flat/up = {share.round(3).tolist()}")

    test_x, test_y = splits["test"]
    prior = np.bincount(splits["train"][1], minlength=3) / len(splits["train"][1])
    results = {"class_prior": score(np.tile(prior, (len(test_y), 1)), test_y)}

    print("\n-- baseline: logistic regression on the latest trade")
    linear = fit(build_linear_baseline(splits["train"][0]), splits, args.epochs, args.lr)
    results["linear_last_trade"] = score(linear.predict(test_x, batch_size=1024, verbose=0), test_y)

    print("\n-- model: Conv1D + GRU over the trade window")
    model = fit(build_model(splits["train"][0]), splits, args.epochs, args.lr)
    results["model"] = score(model.predict(test_x, batch_size=1024, verbose=0), test_y)

    print(f"\ntest set ({len(test_y):,} samples, the most recent markets, none seen in training)")
    print(f"{'':20s}{'log loss':>10s}{'accuracy':>10s}{'macro F1':>10s}")
    for name, r in results.items():
        print(f"{name:20s}{r['log_loss']:10.4f}{r['accuracy']:10.4f}{r['macro_f1']:10.4f}")
    print("model confusion matrix (rows = actual, cols = predicted; down/flat/up):")
    print(np.array(results["model"]["confusion"]))

    ARTIFACTS.mkdir(exist_ok=True)
    model.save(ARTIFACTS / "model.keras")
    meta = {
        "window": dataset.WINDOW,
        "horizon_s": args.horizon,
        "threshold": args.threshold,
        "features": dataset.FEATURES,
        "classes": dataset.CLASSES,
        "test_metrics": results,
    }
    (ARTIFACTS / "meta.json").write_text(json.dumps(meta, indent=2))
    print(f"saved model and metadata to {ARTIFACTS}")


if __name__ == "__main__":
    main()
