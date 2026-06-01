"""실시간 봇 진입점 — markPrice + forceOrder + 명령어 폴링.

실행:
    python -m bot.realtime.main

텔레그램 명령어:
    /help /status /summary /pause /resume /stop
"""

from __future__ import annotations

import asyncio
import logging
import sys

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

from .binance_ws import stream
from .commands import BotState, CommandRouter, poll_commands
from .config import (
    DAILY_SUMMARY_HOURS,
    FUNDING_THRESHOLD,
    LEVERAGE,
    LOG_LEVEL,
    MAX_CONCURRENT_PAPER,
    PAPER_TIME_STOP_HOURS,
    PAPER_TRADES_CSV,
    SYMBOL_REFRESH_MINUTES,
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
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)

log = logging.getLogger("seocoinbot.realtime")


async def _run_mark_stream(handler: SignalHandler, state: BotState) -> None:
    log.info("WebSocket 구독: !markPrice@arr@1s (펀딩 spike + paper 모니터링)")
    async for msg in stream("!markPrice@arr@1s"):
        if state.shutdown.is_set():
            return
        try:
            await handler.on_mark_message(msg)
        except Exception as e:
            log.exception("mark handler error: %s", e)


async def _run_liquidation_stream(handler: SignalHandler, state: BotState) -> None:
    log.info("WebSocket 구독: !forceOrder@arr (청산 캐스케이드)")
    async for msg in stream("!forceOrder@arr"):
        if state.shutdown.is_set():
            return
        try:
            await handler.on_liquidation_message(msg)
        except Exception as e:
            log.exception("liquidation handler error: %s", e)


async def _daily_summary_task(paper: PaperTrader, state: BotState) -> None:
    """N시간마다 누적 통계 텔레그램 전송. 0 이면 비활성."""
    if DAILY_SUMMARY_HOURS <= 0:
        log.info("일일 요약 비활성")
        return
    log.info("일일 요약: %d시간 마다", DAILY_SUMMARY_HOURS)
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
                    f"📊 *일일 요약 (Binance)*\n"
                    f"━━━━━━━━━━━━\n"
                    f"_(지난 {DAILY_SUMMARY_HOURS}h 거래 없음)_\n"
                    f"오픈: `{len(paper.open_positions)}` 포지션"
                )
            else:
                pf = "inf" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
                text = (
                    f"📊 *일일 요약 (Binance)*\n"
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
            log.error("일일 요약 전송 실패: %s", e)


async def _refresh_symbols_periodically(handler: SignalHandler, state: BotState) -> None:
    if SYMBOL_REFRESH_MINUTES <= 0:
        log.info("심볼 풀 자동 갱신 비활성")
        return
    log.info("심볼 풀 자동 갱신: %d분 마다", SYMBOL_REFRESH_MINUTES)
    while not state.shutdown.is_set():
        try:
            await asyncio.wait_for(state.shutdown.wait(),
                                   timeout=SYMBOL_REFRESH_MINUTES * 60)
            return  # shutdown
        except asyncio.TimeoutError:
            pass
        try:
            new_syms = await get_top_usdt_perps(TOP_N_SYMBOLS)
            added, removed = handler.update_symbols(new_syms)
            if added or removed:
                log.info("심볼 갱신: +%d -%d (총 %d)",
                         len(added), len(removed), len(handler.symbols))
                if added or removed:
                    msg = f"🔄 *심볼 풀 갱신*\n+`{len(added)}` / -`{len(removed)}` (총 `{len(handler.symbols)}`)"
                    if added:
                        msg += f"\n신규: {', '.join(sorted(added))[:200]}"
                    if removed:
                        msg += f"\n제외: {', '.join(sorted(removed))[:200]}"
                    await tg_send(msg)
        except Exception as e:
            log.error("심볼 갱신 실패: %s", e)


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

    paper = PaperTrader(
        csv_path=PAPER_TRADES_CSV,
        max_concurrent=MAX_CONCURRENT_PAPER,
        time_stop_hours=PAPER_TIME_STOP_HOURS,
    )
    state = BotState()
    pre_stats = paper.stats()

    await tg_send(
        "🤖 *seocoinbot 실시간 봇 시작*\n"
        "━━━━━━━━━━━━\n"
        f"모니터링 심볼: `{len(symbols)}`개\n"
        f"펀딩 임계: `±{FUNDING_THRESHOLD * 100:.3f}%`\n"
        f"기본 레버리지: `{LEVERAGE}x`\n"
        f"동시 보유: `{MAX_CONCURRENT_PAPER}` 포지션\n"
        f"시간 손절: `{PAPER_TIME_STOP_HOURS}h`\n"
        f"━━ 시그널 종류 ━━\n"
        "1. 펀딩비 spike\n"
        "2. 청산 캐스케이드 반발\n"
        f"━━ 누적 통계 ━━\n"
        f"기존 거래: `{pre_stats['total']}`건\n"
        f"승률: `{pre_stats['win_rate']:.1f}%`\n"
        f"━━━━━━━━━━━━\n"
        f"명령어: `/help2`"
    )

    handler = SignalHandler(symbols, paper, state, FUNDING_THRESHOLD, LEVERAGE)
    router = CommandRouter(state, paper, handler=handler)

    tasks = [
        asyncio.create_task(_run_mark_stream(handler, state), name="mark"),
        asyncio.create_task(_run_liquidation_stream(handler, state), name="liq"),
        asyncio.create_task(poll_commands(router, state), name="cmd"),
        asyncio.create_task(_refresh_symbols_periodically(handler, state), name="refresh"),
        asyncio.create_task(_daily_summary_task(paper, state), name="summary"),
    ]

    # shutdown event 대기
    await state.shutdown.wait()

    log.info("종료 시퀀스 시작")
    await tg_send("👋 *봇 종료*\n다시 시작: `python -m bot.realtime.main`")

    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("키보드 인터럽트로 종료")
