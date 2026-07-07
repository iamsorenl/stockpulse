"""Regenerate app/data/symbols.csv from the NASDAQ Trader symbol directory.

Free, no-key source covering NASDAQ + NYSE/AMEX + ETFs. Run occasionally to
refresh the bundled ticker list:

    ./.venv/bin/python -m scripts.build_symbols
"""

from __future__ import annotations

import csv
import urllib.request
from pathlib import Path

_UA = "StockPulse/0.1 (research; iamsorenl@gmail.com)"
_NASDAQ = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
_OTHER = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
_OUT = Path(__file__).resolve().parent.parent / "app" / "data" / "symbols.csv"


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", "replace")


def main() -> None:
    rows: dict[str, str] = {}

    def add(symbol: str, name: str, is_test: str) -> None:
        symbol = (symbol or "").strip()
        if not symbol or is_test == "Y" or "File Creation Time" in symbol:
            return
        rows.setdefault(symbol, (name or "").strip())

    # nasdaqlisted: Symbol|Security Name|Market Category|Test Issue|...|ETF|NextShares
    for line in _fetch(_NASDAQ).splitlines()[1:]:
        p = line.split("|")
        if len(p) >= 4:
            add(p[0], p[1], p[3])
    # otherlisted: ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot|Test Issue|NASDAQ Symbol
    for line in _fetch(_OTHER).splitlines()[1:]:
        p = line.split("|")
        if len(p) >= 7:
            add(p[0], p[1], p[6])

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    with _OUT.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["symbol", "name"])
        for symbol in sorted(rows):
            writer.writerow([symbol, rows[symbol]])

    print(f"wrote {len(rows)} symbols to {_OUT}")


if __name__ == "__main__":
    main()
