"""Ticker search over a bundled static symbol list (SOR-152, backend half).

Why a static list instead of yfinance's search/lookup?
------------------------------------------------------
yfinance's search helpers hit an undocumented Yahoo autocomplete endpoint that
is rate-limited, frequently returns 429/empty, and needs network at request
time — the exact failure mode we're caching price data to avoid. For a search
box we want an answer that is instant, deterministic, and never errors out. A
bundled list of the most-searched US symbols is the simplest reliable approach
and satisfies the contract ("app" -> AAPL/Apple, empty list on no match, never
an error). The list is easy to extend later or swap for a downloaded exchange
master file without touching the endpoint.

Matching: case-insensitive substring on EITHER symbol OR company name. Symbol
matches rank ahead of name-only matches, then alphabetical by symbol. Results
are capped so the dropdown stays small.
"""

from __future__ import annotations

MAX_RESULTS = 20

# (symbol, company name). Popular US large-caps / ETFs — enough to demo the
# contract and cover the common searches. Extend freely.
SYMBOLS: list[tuple[str, str]] = [
    ("AAPL", "Apple Inc."),
    ("MSFT", "Microsoft Corporation"),
    ("GOOGL", "Alphabet Inc. (Class A)"),
    ("GOOG", "Alphabet Inc. (Class C)"),
    ("AMZN", "Amazon.com, Inc."),
    ("NVDA", "NVIDIA Corporation"),
    ("META", "Meta Platforms, Inc."),
    ("TSLA", "Tesla, Inc."),
    ("BRK.B", "Berkshire Hathaway Inc. (Class B)"),
    ("JPM", "JPMorgan Chase & Co."),
    ("V", "Visa Inc."),
    ("MA", "Mastercard Incorporated"),
    ("UNH", "UnitedHealth Group Incorporated"),
    ("HD", "The Home Depot, Inc."),
    ("PG", "The Procter & Gamble Company"),
    ("JNJ", "Johnson & Johnson"),
    ("XOM", "Exxon Mobil Corporation"),
    ("CVX", "Chevron Corporation"),
    ("KO", "The Coca-Cola Company"),
    ("PEP", "PepsiCo, Inc."),
    ("COST", "Costco Wholesale Corporation"),
    ("WMT", "Walmart Inc."),
    ("BAC", "Bank of America Corporation"),
    ("WFC", "Wells Fargo & Company"),
    ("DIS", "The Walt Disney Company"),
    ("NFLX", "Netflix, Inc."),
    ("ADBE", "Adobe Inc."),
    ("CRM", "Salesforce, Inc."),
    ("ORCL", "Oracle Corporation"),
    ("INTC", "Intel Corporation"),
    ("AMD", "Advanced Micro Devices, Inc."),
    ("QCOM", "QUALCOMM Incorporated"),
    ("CSCO", "Cisco Systems, Inc."),
    ("TXN", "Texas Instruments Incorporated"),
    ("IBM", "International Business Machines Corporation"),
    ("PYPL", "PayPal Holdings, Inc."),
    ("UBER", "Uber Technologies, Inc."),
    ("ABNB", "Airbnb, Inc."),
    ("SHOP", "Shopify Inc."),
    ("SQ", "Block, Inc."),
    ("PLTR", "Palantir Technologies Inc."),
    ("COIN", "Coinbase Global, Inc."),
    ("BA", "The Boeing Company"),
    ("GE", "General Electric Company"),
    ("F", "Ford Motor Company"),
    ("GM", "General Motors Company"),
    ("T", "AT&T Inc."),
    ("VZ", "Verizon Communications Inc."),
    ("PFE", "Pfizer Inc."),
    ("MRK", "Merck & Co., Inc."),
    ("ABBV", "AbbVie Inc."),
    ("LLY", "Eli Lilly and Company"),
    ("NKE", "NIKE, Inc."),
    ("SBUX", "Starbucks Corporation"),
    ("MCD", "McDonald's Corporation"),
    ("SPY", "SPDR S&P 500 ETF Trust"),
    ("VOO", "Vanguard S&P 500 ETF"),
    ("QQQ", "Invesco QQQ Trust"),
    ("VTI", "Vanguard Total Stock Market ETF"),
    ("DIA", "SPDR Dow Jones Industrial Average ETF Trust"),
]


def search_symbols(query: str) -> list[dict[str, str]]:
    """Return up to MAX_RESULTS {"symbol","name"} dicts matching `query`.

    Case-insensitive substring match on symbol OR name. Empty/whitespace query
    yields an empty list. Never raises — no-match simply returns [].
    """
    q = (query or "").strip().lower()
    if not q:
        return []

    symbol_hits: list[dict[str, str]] = []
    name_hits: list[dict[str, str]] = []
    for symbol, name in SYMBOLS:
        in_symbol = q in symbol.lower()
        in_name = q in name.lower()
        if not (in_symbol or in_name):
            continue
        entry = {"symbol": symbol, "name": name}
        # Symbol matches are the stronger signal; surface them first.
        (symbol_hits if in_symbol else name_hits).append(entry)

    symbol_hits.sort(key=lambda e: e["symbol"])
    name_hits.sort(key=lambda e: e["symbol"])
    return (symbol_hits + name_hits)[:MAX_RESULTS]
