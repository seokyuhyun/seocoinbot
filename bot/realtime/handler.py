"""SignalHandler — !markPrice@arr@1s 단일 스트림을 받아:
1) 보유 paper position 마다 mark 으로 TP/SL/TIME_STOP 체크 → 청산 알림
2) 펀딩비 spike 감지 (top-N 심볼만) → 시그널 알림 + paper 진입

펀딩비 spike 중복 방지: 같은 정산 기간(8h) 내 같은 심볼 1회만.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Iterable

from .config import FUNDING_THRESHOLD, LEVERAGE
from .levels import calculate_levels, format_signal
from .paper_trader import PaperTrader, format_close_alert
from .telegram import send as tg_send

log = logging.getLogger(__name__)


class SignalHandler:
    def __init__(
        self,
        symbols: Iterable[str],
        paper_trader: PaperTrader,
        funding_threshold: float = FUNDING_THRESHOLD,
        leverage: int = LEVERAGE,
    ) -> None:
        self.symbols = set(symbols)
        self.paper = paper_trader
        self.threshold = funding_threshold
        self.leverage = leverage
        # 정산 기간(=next_funding_ts) 별로 이미 알림 보낸 심볼 기록
        self._alerted_for_period: dict[str, int] = {}

    async def on_message(self, msg) -> None:
        if isinstance(msg, list):
            for item in msg:
                await self._handle(item)
        elif isinstance(msg, dict):
            await self._handle(msg)

    async def _handle(self, item: dict) -> None:
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

        # 1) Paper position 업데이트 — 보유 중인 심볼만
        events = self.paper.on_mark(sym, mark, ts_now)
        for ev in events:
            if ev[0] == "close":
                _, reason, _ = ev
                # 청산된 position 은 history 마지막 원소
                pos = self.paper.history[-1]
                stats = self.paper.stats()
                text = format_close_alert(pos, reason, stats)
                await tg_send(text)

        # 2) 펀딩 spike 감지 (top-N 심볼만)
        if sym not in self.symbols:
            return
        if abs(funding) < self.threshold:
            return
        if self._alerted_for_period.get(sym) == next_ts:
            return

        # 진입 방향: 펀딩 양수 (롱 과열) → SHORT, 음수 → LONG
        side = "short" if funding > 0 else "long"
        levels = calculate_levels(mark, side, funding)

        # paper 진입 시도
        pos = self.paper.open_position(
            symbol=sym, side=side, entry_price=mark, levels=levels,
            leverage=self.leverage, funding=funding, ts=ts_now,
        )
        opened = pos is not None

        # 다음 정산까지 남은 분
        next_dt = dt.datetime.fromtimestamp(next_ts / 1000, tz=dt.timezone.utc)
        minutes_to = max(0, int((next_dt - ts_now).total_seconds() // 60))

        text = format_signal(
            symbol=sym, side=side, entry=mark, levels=levels,
            leverage=self.leverage, funding=funding,
            minutes_to_funding=minutes_to,
        )
        if not opened:
            text += "\n_(paper: 이미 보유 중 또는 동시보유 한도 도달 — 가상 진입 스킵)_"

        log.info("[SIGNAL] %s %s entry=%g funding=%+.4f%% (paper=%s)",
                 sym, side.upper(), mark, funding * 100, opened)
        await tg_send(text)

        # 중복 방지 마킹
        self._alerted_for_period[sym] = next_ts
