"""Ticker search over a bundled US-symbol directory (SOR-152 / SOR-168).

Source: NASDAQ Trader symbol directory (nasdaqlisted.txt + otherlisted.txt),
covering NASDAQ + NYSE/AMEX + ETFs (~13k symbols). Bundled as a static CSV
(app/data/symbols.csv) so search is instant, deterministic, offline, and never
rate-limited — unlike yfinance's network autocomplete. Regenerate the CSV with
scripts/build_symbols.py when refreshing the list.

Matching (case-insensitive), best-ranked first:
  1. exact symbol   2. symbol prefix   3. symbol substring   4. company name
Within each tier, alphabetical by symbol. Results capped so the dropdown stays
small. Note: the UI also lets users submit any raw ticker directly, so symbols
missing from this list are still reachable — this list only powers suggestions.
"""

from __future__ import annotations

import csv
from pathlib import Path

MAX_RESULTS = 20

_DATA_FILE = Path(__file__).parent / "data" / "symbols.csv"

# Minimal fallback if the bundled CSV is missing (keeps search functional).
_FALLBACK: list[tuple[str, str]] = [
    ("AAPL", "Apple Inc."),
    ("MSFT", "Microsoft Corporation"),
    ("GOOGL", "Alphabet Inc."),
    ("AMZN", "Amazon.com, Inc."),
    ("NVDA", "NVIDIA Corporation"),
    ("META", "Meta Platforms, Inc."),
    ("TSLA", "Tesla, Inc."),
    ("SPY", "SPDR S&P 500 ETF Trust"),
]

# Noise suffixes trimmed from security names for a cleaner dropdown.
_NAME_NOISE = (
    " - Class A Common Stock",
    " - Class B Common Stock",
    " - Common Stock",
    " Common Stock",
    " - Common Shares",
    " Common Shares",
)


def _clean_name(name: str) -> str:
    name = name.strip()
    for suffix in _NAME_NOISE:
        if name.endswith(suffix):
            return name[: -len(suffix)].strip()
    return name


def _load_symbols() -> list[tuple[str, str]]:
    if not _DATA_FILE.exists():
        return _FALLBACK
    out: list[tuple[str, str]] = []
    with _DATA_FILE.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            symbol = (row.get("symbol") or "").strip()
            if symbol:
                out.append((symbol, _clean_name(row.get("name") or "")))
    return out or _FALLBACK


SYMBOLS: list[tuple[str, str]] = _load_symbols()


def search_symbols(query: str) -> list[dict[str, str]]:
    """Return up to MAX_RESULTS {"symbol","name"} dicts matching `query`.

    Case-insensitive, ranked exact-symbol > symbol-prefix > symbol-substring >
    name-substring. Empty/whitespace query yields []. Never raises.
    """
    q = (query or "").strip().lower()
    if not q:
        return []

    exact: list[dict[str, str]] = []
    prefix: list[dict[str, str]] = []
    sym_sub: list[dict[str, str]] = []
    name_sub: list[dict[str, str]] = []
    for symbol, name in SYMBOLS:
        s = symbol.lower()
        entry = {"symbol": symbol, "name": name}
        if s == q:
            exact.append(entry)
        elif s.startswith(q):
            prefix.append(entry)
        elif q in s:
            sym_sub.append(entry)
        elif q in name.lower():
            name_sub.append(entry)

    for bucket in (prefix, sym_sub, name_sub):
        bucket.sort(key=lambda e: e["symbol"])
    return (exact + prefix + sym_sub + name_sub)[:MAX_RESULTS]
