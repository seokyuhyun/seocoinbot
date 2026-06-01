"""Upbit 봇 진입점.

실행:
    python -m bot.upbit.main

요구사항:
    .env: UPBIT_TELEGRAM_BOT_TOKEN, UPBIT_TELEGRAM_CHAT_IDS
    pip: websockets, httpx, python-dotenv
"""

from __future__ import annotations

import asyncio
import logging
import sys

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

from .commands import BotState, CommandRouter, poll_commands
from .config import (
    CANDLE_POLL_INTERVAL_SEC,
    DAILY_SUMMARY_HOURS,
    LEVERAGE,
    LOG_LEVEL,
    MAX_CONCURRENT,
    PAPER_TRADES_CSV,
    SYMBOL_REFRESH_MINUTES,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_IDS,
    TIME_STOP_HOURS,
    TOP_N_SYMBOLS,
    VOLUME_AVG_MINUTES,
    VOLUME_SPIKE_MULT,
)
from .handler import UpbitHandler
from .paper_trader import PaperTrader
from .symbol_picker import get_top_krw_markets
from .telegram import send as tg_send
from .upbit_ws import ticker_stream
from .volume_detector import VolumeSpikeDetector

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)

log = logging.getLogger("seocoinbot.upbit")


async def _run_ws(handler: UpbitHandler, markets: list[str], state: BotState) -> None:
    log.info("Upbit WS ticker 구독 (%d 마켓)", len(markets))
    async for msg in ticker_stream(markets):
        if state.shutdown.is_set():
            return
        try:
            await handler.on_ticker_message(msg)
        except Exception as e:
            log.exception("ws handler error: %s", e)


async def _run_detector(detector: VolumeSpikeDetector, handler: UpbitHandler,
                        state: BotState) -> None:
    log.info("Upbit volume detector 시작")

    async def callback(sig):
        if not state.shutdown.is_set():
            await handler.on_volume_signal(sig)

    await detector.run_periodically(callback)


async def _daily_summary_task(paper: PaperTrader, state: BotState) -> None:
    """N시간마다 누적 통계 텔레그램 전송."""
    if DAILY_SUMMARY_HOURS <= 0:
        log.info("Upbit 일일 요약 비활성")
        return
    log.info("Upbit 일일 요약: %d시간 마다", DAILY_SUMMARY_HOURS)
    while not state.shutdown.is_set():
        try:
            await asyncio.wait_for(state.shutdown.wait(),
                                   timeout=DAILY_SUMMARY_HOURS * 3600)
            return
        except asyncio.TimeoutError:
            pass
        try:
            s = paper.stats()
            if s["total"] == 0:
                text = (
                    f"📊 *일일 요약 (Upbit)*\n"
                    f"━━━━━━━━━━━━\n"
                    f"_(지난 {DAILY_SUMMARY_HOURS}h 거래 없음)_\n"
                    f"오픈: `{len(paper.open_positions)}` 포지션"
                )
            else:
                pf = "inf" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
                text = (
                    f"📊 *일일 요약 (Upbit)*\n"
                    f"━━━━━━━━━━━━\n"
                    f"누적 거래: `{s['total']}` "
                    f"(승 `{s['wins']}` · 패 `{s['losses']}` · BE `{s['breakeven']}`)\n"
                    f"승률: `{s['win_rate']:.1f}%`\n"
                    f"평균: `{s['avg_pct']:+.3f}%/거래`\n"
                    f"PF: `{pf}`\n"
                    f"누적 수익: `{s['total_pct']:+.2f}%`\n"
                    f"오픈 중: `{len(paper.open_positions)}` 포지션\n"
                    f"종료: TP_ALL `{s['tp_all']}` · "
                    f"SL `{s['sl']}` · TIME `{s['time_stop']}`"
                )
            await tg_send(text)
        except Exception as e:
            log.error("Upbit 일일 요약 실패: %s", e)


async def _refresh_markets_periodically(detector: VolumeSpikeDetector,
                                        state: BotState) -> None:
    if SYMBOL_REFRESH_MINUTES <= 0:
        return
    log.info("Upbit 마켓 자동 갱신: %d분 마다", SYMBOL_REFRESH_MINUTES)
    while not state.shutdown.is_set():
        try:
            await asyncio.wait_for(state.shutdown.wait(),
                                   timeout=SYMBOL_REFRESH_MINUTES * 60)
            return
        except asyncio.TimeoutError:
            pass
        try:
            new_markets = await get_top_krw_markets(TOP_N_SYMBOLS)
            added, removed = detector.update_markets(new_markets)
            if added or removed:
                log.info("Upbit 마켓 갱신: +%d -%d (총 %d)",
                         len(added), len(removed), len(detector.markets))
                msg_text = (
                    f"🔄 *Upbit 마켓 풀 갱신*\n"
                    f"+`{len(added)}` / -`{len(removed)}` (총 `{len(detector.markets)}`)"
                )
                if added:
                    msg_text += f"\n신규: {', '.join(sorted(added))[:200]}"
                if removed:
                    msg_text += f"\n제외: {', '.join(sorted(removed))[:200]}"
                await tg_send(msg_text)
        except Exception as e:
            log.error("마켓 갱신 실패: %s", e)


async def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        log.error("UPBIT_TELEGRAM_BOT_TOKEN 누락 — .env 확인")
        sys.exit(1)
    if not TELEGRAM_CHAT_IDS:
        log.error("UPBIT_TELEGRAM_CHAT_IDS 누락 — .env 확인")
        sys.exit(1)
    log.info("Upbit Telegram chat 수: %d", len(TELEGRAM_CHAT_IDS))

    log.info("Upbit 상위 %d KRW 마켓 조회...", TOP_N_SYMBOLS)
    markets = await get_top_krw_markets(TOP_N_SYMBOLS)

    paper = PaperTrader(
        csv_path=PAPER_TRADES_CSV,
        max_concurrent=MAX_CONCURRENT,
        time_stop_hours=TIME_STOP_HOURS,
    )
    state = BotState()
    pre_stats = paper.stats()
    detector = VolumeSpikeDetector(markets)

    await tg_send(
        "🤖 *Upbit 실시간 봇 시작*\n"
        "━━━━━━━━━━━━\n"
        f"모니터 마켓: `{len(markets)}` 개 (KRW)\n"
        f"거래량 spike 임계: `×{VOLUME_SPIKE_MULT:.1f}` (1분 vs {VOLUME_AVG_MINUTES}분 평균)\n"
        f"REST 폴링 주기: `{CANDLE_POLL_INTERVAL_SEC}s`\n"
        f"동시 보유: `{MAX_CONCURRENT}` 포지션\n"
        f"시간 손절: `{TIME_STOP_HOURS}h`\n"
        f"━━ 시그널 ━━\n"
        f"1. 거래량 spike (현물 LONG only)\n"
        f"━━ 누적 통계 ━━\n"
        f"기존 거래: `{pre_stats['total']}`건\n"
        f"승률: `{pre_stats['win_rate']:.1f}%`\n"
        f"━━━━━━━━━━━━\n"
        f"명령어: `/help`"
    )

    handler = UpbitHandler(paper, state)
    router = CommandRouter(state, paper, detector=detector)

    tasks = [
        asyncio.create_task(_run_ws(handler, markets, state), name="ws"),
        asyncio.create_task(_run_detector(detector, handler, state), name="detect"),
        asyncio.create_task(poll_commands(router, state), name="cmd"),
        asyncio.create_task(_refresh_markets_periodically(detector, state), name="refresh"),
        asyncio.create_task(_daily_summary_task(paper, state), name="summary"),
    ]

    await state.shutdown.wait()

    log.info("Upbit 봇 종료 시퀀스")
    await tg_send("👋 *Upbit 봇 종료*\n재시작: `python -m bot.upbit.main`")
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("키보드 인터럽트로 종료")
