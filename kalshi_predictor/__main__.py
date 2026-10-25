"""Usage: python -m kalshi_predictor [command] [options]

Commands:
  dashboard   open the web dashboard (the default)
  predict     predict a market, event or series from the terminal
  fetch       download public trade history to train on
  train       train the model on the downloaded trades

Run a command with --help to see its options.
"""
import importlib
import os
import sys

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

COMMANDS = {
    "dashboard": "dashboard",
    "predict": "predict",
    "fetch": "fetch_data",
    "train": "train",
}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return
    command = "dashboard"
    if argv and not argv[0].startswith("-"):
        command = argv.pop(0)
    if command not in COMMANDS:
        print(f"Unknown command {command!r}.\n\n{__doc__.strip()}", file=sys.stderr)
        raise SystemExit(2)
    # Imported lazily so --help does not wait for TensorFlow to load.
    module = importlib.import_module(f".{COMMANDS[command]}", __package__)
    sys.argv = [f"kalshi_predictor {command}", *argv]
    module.main()


if __name__ == "__main__":
    main()
