#!/usr/bin/env python3
import time, requests

BASE = "https://api.elections.kalshi.com/trade-api/v2"

def markets_closing_between(min_minutes=15, max_hours=24):
    now = int(time.time())
    min_ts = now + min_minutes * 60
    max_ts = now + max_hours * 3600

    out = []
    cursor = None

    while True:
        params = {
            "min_close_ts": min_ts,
            "max_close_ts": max_ts,
            "limit": 1000,
        }
        if cursor:
            params["cursor"] = cursor

        r = requests.get(f"{BASE}/markets", params=params, timeout=30)
        r.raise_for_status()
        data = r.json()

        markets = data.get("markets", [])
        out.extend(markets)

        cursor = data.get("cursor")
        if not cursor:
            break

    # IMPORTANT: API may return "active" for tradable/open markets
    tradable = [m for m in out if m.get("status") in ("active", "open")]

    # Sort by close time if present
    tradable.sort(key=lambda m: m.get("close_time") or "")

    return tradable

if __name__ == "__main__":
    mkts = markets_closing_between(15, 24)
    print(f"Found {len(mkts)} tradable markets closing in 15m..24h")
    for m in mkts[:30]:
        print(f"{m.get('ticker')} | status={m.get('status')} | closes={m.get('close_time')} | {m.get('title')}")
