#!/usr/bin/env python3
"""Resolve unverified US equity cashtags with EODHD instrument metadata.

The resolver is deliberately fail-closed. It only applies a mapping when the
provider returns one exact, primary US common-stock result whose company name
matches the company hint extracted from the source post. Ambiguous and failed
lookups remain in ticker_review.json and receive an auditable reason here.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests


SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "data"
REVIEW_PATH = DATA_DIR / "ticker_review.json"
TMAP_PATH = DATA_DIR / "ticker_map.json"
AUDIT_PATH = DATA_DIR / "ticker_resolution_eodhd.json"
SEARCH_URL = "https://eodhd.com/api/search/{query}"
US_SYMBOL_RE = re.compile(r"^[A-Z]{1,5}(?:[.-][A-Z])?$")
COMPANY_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "company", "co", "ltd",
    "limited", "plc", "holdings", "holding", "group", "sa", "nv",
}


def load_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def name_tokens(value):
    tokens = re.findall(r"[a-z0-9]+", (value or "").lower())
    return [token for token in tokens if token not in COMPANY_SUFFIXES]


def company_names_match(hint, provider_name):
    hint_tokens = name_tokens(hint)
    provider_tokens = name_tokens(provider_name)
    if not hint_tokens or not provider_tokens:
        return False
    hint_text = "".join(hint_tokens)
    provider_text = "".join(provider_tokens)
    provider_initials = "".join(token[0] for token in provider_tokens)
    return (
        hint_text == provider_text
        or hint_text in provider_text
        or provider_text in hint_text
        or (len(hint_tokens) == 1 and hint_text == provider_initials)
    )


def has_verified_mapping(symbol, ticker_map):
    entry = ticker_map.get(symbol)
    if isinstance(entry, dict) and all(
        (entry.get("verified"), entry.get("exchange"), entry.get("currency"), entry.get("price_symbol"))
    ):
        return True
    raw_confirmed = ticker_map.get("_us_confirmed") or []
    confirmed = raw_confirmed if isinstance(raw_confirmed, list) else str(raw_confirmed).split()
    return symbol in confirmed


def select_verified_us_equity(symbol, company_hint, results):
    if not US_SYMBOL_RE.fullmatch(symbol or ""):
        return None, "not_a_conventional_us_cashtag"
    if not company_hint:
        return None, "missing_company_name_hint"

    matches = []
    for row in results if isinstance(results, list) else []:
        asset_type = str(row.get("Type") or "").lower()
        if (
            str(row.get("Code") or "").upper() == symbol
            and str(row.get("Exchange") or "").upper() == "US"
            and str(row.get("Currency") or "").upper() == "USD"
            and row.get("isPrimary") is True
            and "stock" in asset_type
            and company_names_match(company_hint, row.get("Name"))
        ):
            matches.append(row)
    if len(matches) != 1:
        return None, "no_unique_primary_us_equity_match"
    return matches[0], None


class EodhdSearchClient:
    def __init__(self, api_token, request_get=requests.get):
        if not api_token:
            raise ValueError("EODHD_API_KEY is not set")
        self.api_token = api_token
        self.request_get = request_get

    def search(self, symbol):
        response = self.request_get(
            SEARCH_URL.format(query=symbol),
            params={"api_token": self.api_token, "fmt": "json", "type": "stock", "limit": 10},
            timeout=30,
        )
        if response.status_code == 401:
            raise RuntimeError("EODHD Search API rejected EODHD_API_KEY")
        if response.status_code == 429:
            raise RuntimeError("EODHD Search API rate limited the request")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError("EODHD Search API returned a non-list payload")
        return payload


def registry_entry(symbol, row):
    return {
        "instrument_id": f"US:{symbol}",
        "company": row["Name"],
        "exchange": "US",
        "market": "US",
        "country": row.get("Country") or "USA",
        "currency": "USD",
        "price_symbol": symbol,
        "verified": True,
        "verification_source": "EODHD Search API",
        "isin": row.get("ISIN"),
    }


def resolve(review, ticker_map, client, limit=0):
    rows = review.get("unverified") or review.get("unmapped") or []
    work = [
        row for row in rows
        if US_SYMBOL_RE.fullmatch(str(row.get("symbol") or ""))
        and row.get("company_name_hint")
        and not has_verified_mapping(row.get("symbol"), ticker_map)
    ]
    work.sort(key=lambda row: (-(row.get("mentions") or 0), row.get("symbol") or ""))
    if limit:
        work = work[:limit]

    resolved = {}
    audit_rows = []
    for item in work:
        symbol = str(item.get("symbol") or "").upper()
        hint = item.get("company_name_hint")
        try:
            results = client.search(symbol)
            match, reason = select_verified_us_equity(symbol, hint, results)
        except Exception as exc:
            match, reason = None, f"provider_error:{type(exc).__name__}:{exc}"
        if match:
            resolved[symbol] = registry_entry(symbol, match)
        audit_rows.append({
            "symbol": symbol,
            "company_name_hint": hint,
            "status": "verified" if match else "needs_review",
            "reason": reason,
            "provider_match": match,
        })
    return resolved, audit_rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write verified identities into data/ticker_map.json")
    parser.add_argument("--limit", type=int, default=10, help="maximum EODHD Search calls per run (default: 10)")
    args = parser.parse_args()

    api_token = (os.environ.get("EODHD_API_KEY") or "").strip()
    if not api_token:
        print("ERROR: EODHD_API_KEY is not set", file=sys.stderr)
        return 2

    review = load_json(REVIEW_PATH, {})
    ticker_map = load_json(TMAP_PATH, {})
    resolved, rows = resolve(review, ticker_map, EodhdSearchClient(api_token), args.limit)
    audit = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "applied": bool(args.apply),
        "resolved_count": len(resolved),
        "results": rows,
    }
    save_json(AUDIT_PATH, audit)

    if args.apply and resolved:
        ticker_map.update(resolved)
        save_json(TMAP_PATH, ticker_map)

    print(f"EODHD identity resolution: {len(resolved)} verified, {len(rows) - len(resolved)} need review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
