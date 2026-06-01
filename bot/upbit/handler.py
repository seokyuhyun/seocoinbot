"""Upbit 시그널 핸들러 — WebSocket price 추적 + volume detector 콜백."""

from __future__ import annotations

import datetime as dt
import logging

from .commands import BotState
from .levels import calculate_volume_spike_levels, format_signal
from .paper_trader import PaperTrader, format_close_alert
from .telegram import send as tg_send

log = logging.getLogger(__name__)


class UpbitHandler:
    def __init__(self, paper: PaperTrader, state: BotState) -> None:
        self.paper = paper
        self.state = state

    # WebSocket ticker 메시지 → paper position TP/SL 체크
    async def on_ticker_message(self, msg: dict) -> None:
        code = msg.get("cd") or msg.get("code")
        if not code:
            return
        try:
            price = float(msg.get("tp") or msg.get("trade_price", 0))
        except (TypeError, ValueError):
            return
        if price <= 0:
            return

        ts_now = dt.datetime.now(dt.timezone.utc)
        events = self.paper.on_price(code, price, ts_now)
        for ev in events:
            if ev[0] == "close":
                _, reason, _ = ev
                pos = self.paper.history[-1]
                stats = self.paper.stats()
                await tg_send(format_close_alert(pos, reason, stats))

    # volume detector → 시그널 발사
    async def on_volume_signal(self, sig: dict) -> None:
        if self.state.paused:
            return
        market = sig["market"]
        if market in self.paper.open_positions:
            return   # 이미 보유

        entry = sig["trade_price"]
        ratio = sig["ratio"]
        levels = calculate_volume_spike_levels(entry, ratio)

        ts_now = dt.datetime.now(dt.timezone.utc)
        meta = (
            f"ratio={ratio:.2f},"
            f"cur={sig['cur_vol_krw']:.0f},"
            f"avg={sig['avg_vol_krw']:.0f}"
        )
        pos = self.paper.open_position(
            market=market, entry_price=entry, levels=levels,
            ts=ts_now, signal_type="volume_spike", signal_meta=meta,
        )
        opened = pos is not None

        text = format_signal(
            market=market, entry=entry, levels=levels, ratio=ratio,
            cur_vol_krw=sig["cur_vol_krw"], avg_vol_krw=sig["avg_vol_krw"],
        )
        if not opened:
            text += "\n_(paper: 이미 보유 또는 동시보유 한도)_"

        log.info("[VOLUME] %s LONG entry=%g ratio=×%.1f (paper=%s)",
                 market, entry, ratio, opened)
        await tg_send(text)
