"""SignalHandler — markPrice + forceOrder 두 스트림을 모두 받는 핸들러.

markPrice 스트림 (매 1초):
  1) 보유 paper position → TP/SL/TIME_STOP 체크 → 청산 알림
  2) 캐스케이드 detector 에 가격 스냅 공급
  3) 펀딩비 spike 감지 → 시그널 알림 + paper 진입

forceOrder 스트림 (이벤트 기반):
  1) 캐스케이드 detector 에 청산 이벤트 공급
  2) 캐스케이드 트리거 시 → 시그널 알림 + paper 진입
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Iterable

from .commands import BotState
from .config import FUNDING_THRESHOLD, LEVERAGE
from .levels import (
    calculate_cascade_levels,
    calculate_levels,
    format_cascade_signal,
    format_signal,
)
from .liquidation_alerts import CascadeDetector
from .paper_trader import PaperTrader, format_close_alert
from .telegram import send as tg_send

log = logging.getLogger(__name__)


class SignalHandler:
    def __init__(
        self,
        symbols: Iterable[str],
        paper_trader: PaperTrader,
        state: BotState,
        funding_threshold: float = FUNDING_THRESHOLD,
        leverage: int = LEVERAGE,
    ) -> None:
        self.symbols = set(symbols)
        self.paper = paper_trader
        self.state = state
        self.threshold = funding_threshold
        self.leverage = leverage
        self._alerted_funding_period: dict[str, int] = {}
        self.cascade = CascadeDetector(symbols)

    # ── markPrice 스트림 핸들러 ────────────────────────────
    async def on_mark_message(self, msg) -> None:
        if isinstance(msg, list):
            for item in msg:
                await self._handle_mark(item)
        elif isinstance(msg, dict):
            await self._handle_mark(msg)

    async def _handle_mark(self, item: dict) -> None:
        sym = item.get("s")
        if not sym:
            return
        try:
            mark = float(item.get("p", 0))
            funding = float(item.get("r", 0))
            next_ts = int(item.get("T", 0))
        except (TypeError, ValueError):
            return
        if mark <= 0:
            return

        ts_now = dt.datetime.now(dt.timezone.utc)

        # 1) paper position 업데이트
        events = self.paper.on_mark(sym, mark, ts_now)
        for ev in events:
            if ev[0] == "close":
                _, reason, _ = ev
                pos = self.paper.history[-1]
                stats = self.paper.stats()
                await tg_send(format_close_alert(pos, reason, stats))

        # 2) 캐스케이드 detector 에 가격 스냅 공급 (paused 여부 무관)
        self.cascade.on_mark(sym, mark, ts_now)

        # 3) 새 시그널 — paused 면 발사 안 함
        if self.state.paused:
            return
        if sym not in self.symbols:
            return
        if abs(funding) < self.threshold:
            return
        if self._alerted_funding_period.get(sym) == next_ts:
            return

        side = "short" if funding > 0 else "long"
        levels = calculate_levels(mark, side, funding)
        pos = self.paper.open_position(
            symbol=sym, side=side, entry_price=mark, levels=levels,
            leverage=self.leverage, funding=funding, ts=ts_now,
            signal_type="funding_spike",
            signal_meta=f"funding={funding*100:+.4f}%",
        )
        opened = pos is not None

        next_dt = dt.datetime.fromtimestamp(next_ts / 1000, tz=dt.timezone.utc)
        minutes_to = max(0, int((next_dt - ts_now).total_seconds() // 60))

        text = format_signal(
            symbol=sym, side=side, entry=mark, levels=levels,
            leverage=self.leverage, funding=funding,
            minutes_to_funding=minutes_to,
        )
        if not opened:
            text += "\n_(paper: 이미 보유 중 또는 동시보유 한도 도달)_"

        log.info("[FUNDING] %s %s entry=%g funding=%+.4f%% (paper=%s)",
                 sym, side.upper(), mark, funding * 100, opened)
        await tg_send(text)
        self._alerted_funding_period[sym] = next_ts

    # ── forceOrder 스트림 핸들러 ───────────────────────────
    async def on_liquidation_message(self, msg) -> None:
        # detector 윈도우는 paused 여부와 무관하게 항상 업데이트 (시그널 발사만 차단)
        signal = self.cascade.on_force_order(msg)
        if signal is None:
            return
        if self.state.paused:
            return

        ts_now = dt.datetime.now(dt.timezone.utc)
        sym = signal["symbol"]
        side = signal["side"]
        mark = signal["mark"]
        total_usd = signal["total_liq_usd"]

        levels = calculate_cascade_levels(mark, side, total_usd)

        # paper 진입
        meta = (
            f"liq={total_usd:.0f}USD,"
            f"lop={signal['lopsided_side']}{signal['lopsided_pct']:.0f}%,"
            f"dp={signal['price_change_pct']:+.2f}%"
        )
        pos = self.paper.open_position(
            symbol=sym, side=side, entry_price=mark, levels=levels,
            leverage=self.leverage, funding=0.0, ts=ts_now,
            signal_type="liquidation_cascade",
            signal_meta=meta,
        )
        opened = pos is not None

        text = format_cascade_signal(
            symbol=sym, side=side, entry=mark, levels=levels,
            leverage=self.leverage,
            total_liq_usd=total_usd,
            lopsided_pct=signal["lopsided_pct"],
            lopsided_side=signal["lopsided_side"],
            price_change_pct=signal["price_change_pct"],
            window_minutes=signal["window_minutes"],
        )
        if not opened:
            text += "\n_(paper: 이미 보유 중 또는 동시보유 한도 도달)_"

        log.info("[CASCADE] %s %s entry=%g liq=$%g %s%.0f%% Δp=%+.2f%% (paper=%s)",
                 sym, side.upper(), mark, total_usd,
                 signal["lopsided_side"], signal["lopsided_pct"],
                 signal["price_change_pct"], opened)
        await tg_send(text)
