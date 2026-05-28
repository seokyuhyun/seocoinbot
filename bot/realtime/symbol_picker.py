"""상위 N USDT 무기한 선물 심볼 조회 — 24h 거래대금 기준."""

from __future__ import annotations

import logging

import httpx

from .config import BINANCE_FAPI

log = logging.getLogger(__name__)


async def get_top_usdt_perps(n: int = 30) -> list[str]:
    """상위 N개 USDT 무기한 심볼 반환 (거래대금 내림차순). BTCUSDT 우선 포함."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        info_r = await client.get(f"{BINANCE_FAPI}/exchangeInfo")
        info_r.raise_for_status()
        info = info_r.json()
        tk_r = await client.get(f"{BINANCE_FAPI}/ticker/24hr")
        tk_r.raise_for_status()
        tickers = tk_r.json()

    valid = set()
    for s in info["symbols"]:
        if (s.get("contractType") == "PERPETUAL"
                and s.get("quoteAsset") == "USDT"
                and s.get("status") == "TRADING"):
            valid.add(s["symbol"])

    rows = []
    for t in tickers:
        sym = t.get("symbol")
        if sym in valid:
            try:
                qv = float(t.get("quoteVolume", 0))
            except (TypeError, ValueError):
                qv = 0
            rows.append((sym, qv))
    rows.sort(key=lambda x: -x[1])
    symbols = [s for s, _ in rows[:n]]
    if "BTCUSDT" not in symbols:
        symbols = ["BTCUSDT"] + symbols[: n - 1]
    log.info("상위 %d 심볼 (거래대금): %s", len(symbols), symbols)
    return symbols
