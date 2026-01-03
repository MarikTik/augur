#!/usr/bin/env python3
"""
Kalshi REST + WebSocket example (latest v2 URLs)

Requires:
  pip install requests cryptography websockets

Notes:
- Public market-data REST endpoints: https://api.elections.kalshi.com/trade-api/v2  (no auth)
- Demo authenticated REST base:      https://demo-api.kalshi.co
- WebSocket (prod): wss://api.elections.kalshi.com/trade-api/ws/v2
- WebSocket (demo): wss://demo-api.kalshi.co/trade-api/ws/v2

Auth headers (REST + WS):
  KALSHI-ACCESS-KEY
  KALSHI-ACCESS-TIMESTAMP   (ms)
  KALSHI-ACCESS-SIGNATURE   (base64 RSA-PSS SHA256 over: timestamp + METHOD + PATH_WITHOUT_QUERY)
For WebSocket path to sign: "/trade-api/ws/v2"
"""

import asyncio
import base64
import datetime as dt
import json
import os
from typing import Dict

import requests
import websockets
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


# ----------------------------
# Config (fill these in)
# ----------------------------
KEY_ID = os.getenv("KALSHI_KEY_ID", "YOUR_KEY_ID_HERE")
PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH", "path/to/kalshi_private_key.pem")

REST_PUBLIC_BASE = "https://api.elections.kalshi.com"
REST_DEMO_BASE = "https://demo-api.kalshi.co"
WS_DEMO_URL = "wss://demo-api.kalshi.co/trade-api/ws/v2"


# ----------------------------
# Signing helpers
# ----------------------------
def load_private_key(file_path: str) -> rsa.RSAPrivateKey:
    with open(file_path, "rb") as f:
        return serialization.load_pem_private_key(
            f.read(),
            password=None,
            backend=default_backend(),
        )


def now_ms() -> str:
    return str(int(dt.datetime.now().timestamp() * 1000))


def sign_pss_sha256(private_key: rsa.RSAPrivateKey, message: str) -> str:
    sig_bytes = private_key.sign(
        message.encode("utf-8"),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(sig_bytes).decode("utf-8")


def build_auth_headers(
    private_key: rsa.RSAPrivateKey,
    key_id: str,
    method: str,
    path: str,
) -> Dict[str, str]:
    """
    Important: sign only the PATH without query params (strip ?...).
    """
    ts = now_ms()
    path_no_query = path.split("?", 1)[0]
    to_sign = ts + method.upper() + path_no_query
    sig = sign_pss_sha256(private_key, to_sign)
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": sig,
    }


# ----------------------------
# REST examples
# ----------------------------
def rest_public_example() -> None:
    # Public endpoint: list markets (no auth). See docs for filters/cursors.
    path = "/trade-api/v2/markets?limit=5"
    url = REST_PUBLIC_BASE + path
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    print("\n[REST public] First 5 markets:")
    for m in data.get("markets", []):
        print(f"  {m.get('ticker')} | {m.get('title')}")


def rest_authenticated_example(private_key: rsa.RSAPrivateKey) -> None:
    # Authenticated demo endpoint example (portfolio balance)
    path = "/trade-api/v2/portfolio/balance"
    headers = build_auth_headers(private_key, KEY_ID, "GET", path)
    url = REST_DEMO_BASE + path
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    print("\n[REST auth demo] Portfolio balance:")
    print(json.dumps(r.json(), indent=2))


# ----------------------------
# WebSocket example
# ----------------------------
async def ws_example(private_key: rsa.RSAPrivateKey) -> None:
    # WebSocket requires the same auth headers, signing: timestamp + "GET" + "/trade-api/ws/v2"
    ws_path_to_sign = "/trade-api/ws/v2"
    ws_headers = build_auth_headers(private_key, KEY_ID, "GET", ws_path_to_sign)

    async with websockets.connect(WS_DEMO_URL, additional_headers=ws_headers) as ws:
        print("\n[WS] Connected.")

        # Subscribe to orderbook deltas for a specific market ticker.
        # Replace with a real open ticker you care about.
        market_ticker = "FED-23DEC-T3.00"  # example format; change this to a current ticker

        sub_msg = {
            "id": 1,
            "cmd": "subscribe",
            "params": {
                "channels": ["orderbook_delta"],
                "market_tickers": [market_ticker],
            },
        }
        await ws.send(json.dumps(sub_msg))
        print(f"[WS] Subscribed to orderbook_delta for {market_ticker}")

        # Read a few messages then exit
        for i in range(10):
            raw = await ws.recv()
            msg = json.loads(raw)
            print(f"[WS] #{i+1}: {msg.get('type')} -> {msg.get('msg', msg)}")


def main() -> None:
    rest_public_example()

    # Only run authenticated examples if you provided credentials
    if "YOUR_KEY_ID_HERE" in KEY_ID or "path/to/" in PRIVATE_KEY_PATH:
        print("\nSkipping authenticated REST + WS examples (set KALSHI_KEY_ID and KALSHI_PRIVATE_KEY_PATH).")
        return

    private_key = load_private_key(PRIVATE_KEY_PATH)
    rest_authenticated_example(private_key)
    asyncio.run(ws_example(private_key))


if __name__ == "__main__":
    main()
