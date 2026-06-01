"""상위 N KRW 마켓 (24h KRW 거래대금 기준)."""

from __future__ import annotations

import logging

from .upbit_rest import get_all_krw_markets, get_ticker

log = logging.getLogger(__name__)


async def get_top_krw_markets(n: int = 30) -> list[str]:
    all_markets = await get_all_krw_markets()
    # 일괄 ticker 조회 — Upbit 는 한 번에 다 받음
    tickers = await get_ticker(all_markets)
    rows = []
    for t in tickers:
        try:
            atp = float(t.get("acc_trade_price_24h", 0))
        except (TypeError, ValueError):
            atp = 0
        rows.append((t["market"], atp))
    rows.sort(key=lambda x: -x[1])
    top = [m for m, _ in rows[:n]]
    if "KRW-BTC" not in top:
        top = ["KRW-BTC"] + top[: n - 1]
    log.info("상위 %d KRW 마켓 (24h 거래대금): %s", len(top), top)
    return top
