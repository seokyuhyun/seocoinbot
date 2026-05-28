"""실시간 봇 진입점.

실행:
    python -m bot.realtime.main

요구사항:
    .env 에 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS 설정.
    pip install websockets httpx python-dotenv
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
    LOG_LEVEL,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_IDS,
    TOP_N_SYMBOLS,
)
from .funding_alerts import FundingAlerter
from .symbol_picker import get_top_usdt_perps
from .telegram import send as tg_send

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# httpx 가 INFO 에서 전체 URL (= 토큰 포함) 출력. 보안상 WARNING 이상으로.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)
log = logging.getLogger("seocoinbot.realtime")


async def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN 누락 — .env 에 설정하세요.")
        sys.exit(1)
    if not TELEGRAM_CHAT_IDS:
        log.error("TELEGRAM_CHAT_IDS 누락 — .env 에 콤마로 구분해 설정.")
        sys.exit(1)
    log.info("Telegram chat 수: %d", len(TELEGRAM_CHAT_IDS))

    log.info("상위 %d USDT 무기한 조회 중...", TOP_N_SYMBOLS)
    symbols = await get_top_usdt_perps(TOP_N_SYMBOLS)

    await tg_send(
        f"🤖 *seocoinbot 시작*\n"
        f"━━━━━━━━━━━━\n"
        f"모니터링 심볼: `{len(symbols)}`개\n"
        f"펀딩 임계: `±{FUNDING_THRESHOLD * 100:.3f}%`\n"
        f"시그널 종류: 펀딩 spike (MVP)\n"
    )

    alerter = FundingAlerter(symbols, FUNDING_THRESHOLD)

    log.info("WebSocket 구독 시작: !markPrice@arr@1s")
    async for msg in stream("!markPrice@arr@1s"):
        try:
            await alerter.on_message(msg)
        except Exception as e:
            log.exception("handler error: %s", e)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("키보드 인터럽트로 종료")
