#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator, Optional, Dict, Any, Tuple

import requests


BASE = "https://api.elections.kalshi.com/trade-api/v2"


# -----------------------------
# Data model
# -----------------------------
@dataclass(frozen=True)
class MarketPrice:
    # prices in cents (0..100) if available
    yes_bid: Optional[int] = None
    yes_ask: Optional[int] = None
    no_bid: Optional[int] = None
    no_ask: Optional[int] = None

    # derived implied probability (%) for YES as a float in [0, 100]
    yes_prob_mid: Optional[float] = None
    yes_prob_low: Optional[float] = None   # from best bid
    yes_prob_high: Optional[float] = None  # from best ask

    def pretty(self) -> str:
        def fmt(x):
            return "?" if x is None else str(x)
        def fpf(x):
            return "?" if x is None else f"{x:.2f}"
        return (
            f"YES bid/ask={fmt(self.yes_bid)}/{fmt(self.yes_ask)} "
            f"NO bid/ask={fmt(self.no_bid)}/{fmt(self.no_ask)} "
            f"YES%~[{fpf(self.yes_prob_low)}, {fpf(self.yes_prob_high)}] mid={fpf(self.yes_prob_mid)}"
        )


@dataclass(frozen=True)
class Market:
    ticker: str
    title: str
    status: str
    close_time: datetime

    # optional metadata (depends on endpoint / fields available)
    event_ticker: Optional[str] = None
    series_ticker: Optional[str] = None

    # pricing / implied probability
    price: MarketPrice = MarketPrice()


# -----------------------------
# Helpers
# -----------------------------
def _to_unix_seconds(dt: datetime) -> int:
    if dt.tzinfo is None:
        raise ValueError("Datetime must be timezone-aware (e.g., timezone.utc).")
    return int(dt.timestamp())


def _parse_iso8601_z(s: str) -> datetime:
    # e.g. "2026-01-03T02:30:00Z"
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _best_bids_from_orderbook(ob: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    """
    Kalshi orderbook endpoint returns *bids* for YES and NO sides (no asks).
    We'll take the best (highest price) bid for each side.
    """
    def best(side_key: str) -> Optional[int]:
        levels = ob.get(side_key) or []
        # Each level is typically [price, quantity] in cents/contracts.
        # Robustly handle dict-ish shapes too.
        best_price = None
        for lvl in levels:
            if isinstance(lvl, (list, tuple)) and len(lvl) >= 1:
                p = lvl[0]
            elif isinstance(lvl, dict) and "price" in lvl:
                p = lvl["price"]
            else:
                continue
            try:
                p_int = int(p)
            except Exception:
                continue
            best_price = p_int if best_price is None else max(best_price, p_int)
        return best_price

    yes_bid = best("yes")
    no_bid = best("no")
    return yes_bid, no_bid


def _derive_prices_and_probs(yes_bid: Optional[int], no_bid: Optional[int],
                             yes_ask: Optional[int] = None, no_ask: Optional[int] = None) -> MarketPrice:
    """
    Binary market mechanics: YES_ask ~= 100 - NO_bid, and NO_ask ~= 100 - YES_bid,
    if asks are not directly provided.
    """
    # Derive missing asks if we have complementary bids
    if yes_ask is None and no_bid is not None:
        yes_ask = 100 - no_bid
    if no_ask is None and yes_bid is not None:
        no_ask = 100 - yes_bid

    # Probability bounds and midpoint for YES
    yes_prob_low = float(yes_bid) if yes_bid is not None else None
    yes_prob_high = float(yes_ask) if yes_ask is not None else None

    yes_prob_mid = None
    if yes_prob_low is not None and yes_prob_high is not None:
        yes_prob_mid = (yes_prob_low + yes_prob_high) / 2.0
    elif yes_prob_low is not None:
        yes_prob_mid = yes_prob_low
    elif yes_prob_high is not None:
        yes_prob_mid = yes_prob_high

    return MarketPrice(
        yes_bid=yes_bid, yes_ask=yes_ask,
        no_bid=no_bid, no_ask=no_ask,
        yes_prob_low=yes_prob_low, yes_prob_high=yes_prob_high,
        yes_prob_mid=yes_prob_mid
    )


# -----------------------------
# Core generator
# -----------------------------
def iter_markets_closing_between(
    start_close: datetime,
    end_close: datetime,
    *,
    base_url: str = BASE,
    status_filter: Optional[str] = None,   # e.g., "active" (what you observed), or None for any
    include_orderbook_fallback: bool = True,
    session: Optional[requests.Session] = None,
) -> Iterator[Market]:
    """
    Yields Market objects for all markets with close_time in [start_close, end_close],
    using public REST. If best bid/ask fields are not present in market objects,
    it can optionally call the public orderbook endpoint per market as a fallback.
    """
    if end_close <= start_close:
        raise ValueError("end_close must be > start_close")

    min_close_ts = _to_unix_seconds(start_close)
    max_close_ts = _to_unix_seconds(end_close)

    sess = session or requests.Session()
    cursor = None

    while True:
        params = {
            "min_close_ts": min_close_ts,
            "max_close_ts": max_close_ts,
            "limit": 1000,
        }
        if cursor:
            params["cursor"] = cursor

        r = sess.get(f"{base_url}/markets", params=params, timeout=30)
        r.raise_for_status()
        data = r.json()

        for m in data.get("markets", []):
            status = m.get("status", "")
            # Your output showed "active" as tradable. Keep it flexible:
            if status_filter is not None and status != status_filter:
                continue

            ticker = m.get("ticker", "")
            title = m.get("title", "")
            close_time_str = m.get("close_time")
            if not (ticker and close_time_str):
                continue

            close_time = _parse_iso8601_z(close_time_str)

            # Try to use pricing fields if present (they may or may not exist in list responses)
            yes_bid = m.get("yes_bid")
            no_bid = m.get("no_bid")
            yes_ask = m.get("yes_ask")
            no_ask = m.get("no_ask")

            # Normalize possible string -> int
            def norm_int(x):
                if x is None:
                    return None
                try:
                    return int(x)
                except Exception:
                    return None

            yes_bid = norm_int(yes_bid)
            no_bid = norm_int(no_bid)
            yes_ask = norm_int(yes_ask)
            no_ask = norm_int(no_ask)

            # If missing bids/asks, optionally fetch orderbook bids (public) and derive asks.
            if include_orderbook_fallback and (yes_bid is None or no_bid is None):
                ob = sess.get(f"{base_url}/markets/{ticker}/orderbook", timeout=30).json()
                yb, nb = _best_bids_from_orderbook(ob)
                yes_bid = yes_bid if yes_bid is not None else yb
                no_bid = no_bid if no_bid is not None else nb

            price = _derive_prices_and_probs(yes_bid=yes_bid, no_bid=no_bid, yes_ask=yes_ask, no_ask=no_ask)

            yield Market(
                ticker=ticker,
                title=title,
                status=status,
                close_time=close_time,
                event_ticker=m.get("event_ticker"),
                series_ticker=m.get("series_ticker"),
                price=price,
            )

        cursor = data.get("cursor")
        if not cursor:
            break


# -----------------------------
# Example usage (your exact window)
# -----------------------------
if __name__ == "__main__":
    now = datetime.now(timezone.utc)
    start = now.replace(microsecond=0)  # absolute start
    # 15 min .. 24h
    start = start + __import__("datetime").timedelta(minutes=15)
    end = start + __import__("datetime").timedelta(hours=24)

    count = 0
    for market in iter_markets_closing_between(
        start, end,
        status_filter="active",                 # based on what you observed
        include_orderbook_fallback=True,        # pulls orderbook only if needed
    ):
        count += 1
        print(
            f"{market.ticker} | status={market.status} | closes={market.close_time.isoformat()} | "
            f"{market.title} | {market.price.pretty()}"
        )
    #     if count >= 30:
    #         break

    # print(f"\nPrinted {min(count, 30)} markets (streaming).")
