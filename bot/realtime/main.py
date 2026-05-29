"""실시간 봇 진입점 — Signal handler + Paper trader.

실행:
    python -m bot.realtime.main

요구사항:
    .env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS
    pip: websockets, httpx, python-dotenv
"""

from __future__ import annotations

import asyncio
import logging
import sys

# UTF-8 출력 (Windows 콘솔 surrogate 회피)
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

from .binance_ws import stream
from .config import (
    FUNDING_THRESHOLD,
    LEVERAGE,
    LOG_LEVEL,
    MAX_CONCURRENT_PAPER,
    PAPER_TIME_STOP_HOURS,
    PAPER_TRADES_CSV,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_IDS,
    TOP_N_SYMBOLS,
)
from .handler import SignalHandler
from .paper_trader import PaperTrader
from .symbol_picker import get_top_usdt_perps
from .telegram import send as tg_send

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# 보안: httpx 가 INFO 에서 전체 URL (= 토큰 포함) 출력
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)

log = logging.getLogger("seocoinbot.realtime")


async def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN 누락 — .env 확인")
        sys.exit(1)
    if not TELEGRAM_CHAT_IDS:
        log.error("TELEGRAM_CHAT_IDS 누락 — .env 확인")
        sys.exit(1)
    log.info("Telegram chat 수: %d", len(TELEGRAM_CHAT_IDS))

    log.info("상위 %d USDT 무기한 조회 중...", TOP_N_SYMBOLS)
    symbols = await get_top_usdt_perps(TOP_N_SYMBOLS)

    # Paper trader 초기화 (기존 CSV 있으면 통계 로드)
    paper = PaperTrader(
        csv_path=PAPER_TRADES_CSV,
        max_concurrent=MAX_CONCURRENT_PAPER,
        time_stop_hours=PAPER_TIME_STOP_HOURS,
    )
    pre_stats = paper.stats()

    await tg_send(
        "🤖 *seocoinbot 실시간 봇 시작*\n"
        "━━━━━━━━━━━━\n"
        f"모니터링 심볼: `{len(symbols)}`개\n"
        f"펀딩 임계: `±{FUNDING_THRESHOLD * 100:.3f}%`\n"
        f"기본 레버리지: `{LEVERAGE}x`\n"
        f"동시 보유: `{MAX_CONCURRENT_PAPER}` 포지션\n"
        f"시간 손절: `{PAPER_TIME_STOP_HOURS}h`\n"
        f"━━ 누적 통계 (재시작 후 복원) ━━\n"
        f"기존 거래: `{pre_stats['total']}`건\n"
        f"승률: `{pre_stats['win_rate']:.1f}%`\n"
    )

    handler = SignalHandler(symbols, paper, FUNDING_THRESHOLD, LEVERAGE)

    log.info("WebSocket 구독 시작: !markPrice@arr@1s")
    async for msg in stream("!markPrice@arr@1s"):
        try:
            await handler.on_message(msg)
        except Exception as e:
            log.exception("handler error: %s", e)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("키보드 인터럽트로 종료")
