"""Upbit REST API 클라이언트 — 마켓 조회, 24h ticker, 1분 캔들."""

from __future__ import annotations

import logging
from typing import Iterable

import httpx

from .config import UPBIT_REST

log = logging.getLogger(__name__)


async def get_all_krw_markets() -> list[str]:
    """전체 KRW 마켓 코드 list."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(f"{UPBIT_REST}/market/all")
        r.raise_for_status()
        data = r.json()
    return [m["market"] for m in data if m["market"].startswith("KRW-")]


async def get_ticker(markets: Iterable[str]) -> list[dict]:
    """주어진 마켓들의 24h ticker 정보."""
    codes = ",".join(markets)
    if not codes:
        return []
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(f"{UPBIT_REST}/ticker", params={"markets": codes})
        r.raise_for_status()
        return r.json()


async def get_minute_candles(market: str, count: int = 21) -> list[dict]:
    """1분 캔들 가져오기. 최근 count 개. 최신 캔들이 [0] index."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(
            f"{UPBIT_REST}/candles/minutes/1",
            params={"market": market, "count": count},
        )
        r.raise_for_status()
        return r.json()
