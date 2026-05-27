"""유튜브 4전략용 백테스트 엔진 — 동적 SL/TP 지원.

기존 backtest/run.py 엔진은 v0.1 의 고정폭 손절(1%) + CCI 0선 청산에
강하게 묶여 있어 재사용 불편. 이 엔진은 전략이 매 신호에서 SL/TP 절대가격을
지정할 수 있도록 일반화한 단순 버전.

규약(기존 엔진과 동일):
- 신호는 캔들 close 에서 생성, 진입은 다음 캔들 open (명세서 §6.1)
- 인트라바 H/L 가 손절·익절가를 관통하면 그 가격에 체결 (명세서 §4)
- 같은 캔들에서 SL/TP 동시 터치 시 SL 우선 (보수)
- 비용: taker 0.04% + slippage 2bp + 펀딩 0.01% (00/08/16 UTC)
- 1차 익절(tp)은 50%, 2차(tp2)는 나머지 50%. tp2 가 None 이면 tp 에서 전량 청산
- 청산 후 1봉 쿨다운, 일일 -3% halt, 연속 손절 2회 halt (설계서 §3)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.run import (  # noqa: E402  공통 상수·유틸 재사용
    DATA_DIR,
    INITIAL_EQUITY,
    POSITION_PCT,
    TAKER_FEE,
    SLIPPAGE,
    FUNDING_RATE,
    FUNDING_HOURS_UTC,
    DAILY_LOSS_HALT_PCT,
    CONSECUTIVE_STOP_HALT,
    COOLDOWN_CANDLES,
    IN_SAMPLE_RATIO,
    load_klines,
    summarize,
    print_report,
)
from strategy.yt_strategies import Signal, StrategyHook  # noqa: E402


@dataclass
class YTTrade:
    side: str
    entry_time: pd.Timestamp
    entry_price: float
    notional: float
    stop_price: float
    tp_price: float
    tp2_price: Optional[float]
    tp1_done: bool = False
    remaining_pct: float = 1.0
    fills: list = field(default_factory=list)


@dataclass
class YTState:
    equity: float = INITIAL_EQUITY
    peak_equity: float = INITIAL_EQUITY
    max_drawdown: float = 0.0
    daily_pnl_pct: float = 0.0
    daily_anchor_equity: float = INITIAL_EQUITY
    current_utc_day: Optional[pd.Timestamp] = None
    consecutive_stops: int = 0
    halt_today: bool = False
    last_exit_idx: int = -10_000
    position: Optional[YTTrade] = None
    pending_signal: Optional[Signal] = None
    closed_trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
    total_fees: float = 0.0
    total_funding: float = 0.0


def _slip(price: float, side: str, opening: bool) -> float:
    if (side == "LONG" and opening) or (side == "SHORT" and not opening):
        return price * (1.0 + SLIPPAGE)
    return price * (1.0 - SLIPPAGE)


def _reset_daily_if_needed(state: YTState, ts: pd.Timestamp) -> None:
    day = ts.normalize()
    if state.current_utc_day is None or day != state.current_utc_day:
        state.current_utc_day = day
        state.daily_pnl_pct = 0.0
        state.daily_anchor_equity = state.equity
        state.halt_today = False
        state.consecutive_stops = 0


def _close_partial(state: YTState, px: float, size_pct: float, reason: str, ts: pd.Timestamp) -> None:
    pos = state.position
    assert pos is not None
    direction = 1 if pos.side == "LONG" else -1
    fill_px = _slip(px, pos.side, opening=False)
    fill_size_usdt = pos.notional * size_pct
    gross = direction * (fill_px - pos.entry_price) / pos.entry_price * fill_size_usdt
    fee = TAKER_FEE * fill_size_usdt
    state.equity += gross - fee
    state.total_fees += fee
    pos.fills.append((ts, fill_px, size_pct, reason, gross, fee, 0.0))
    pos.remaining_pct -= size_pct
    state.daily_pnl_pct = (state.equity - state.daily_anchor_equity) / state.daily_anchor_equity
    if state.daily_pnl_pct <= -DAILY_LOSS_HALT_PCT:
        state.halt_today = True


def _close_remaining(state: YTState, px: float, reason: str, ts: pd.Timestamp, idx: int) -> None:
    pos = state.position
    assert pos is not None
    if pos.remaining_pct > 1e-9:
        _close_partial(state, px, pos.remaining_pct, reason, ts)
    state.closed_trades.append(pos)
    state.position = None
    state.last_exit_idx = idx
    if reason == "STOP_LOSS":
        state.consecutive_stops += 1
        if state.consecutive_stops >= CONSECUTIVE_STOP_HALT:
            state.halt_today = True
    else:
        state.consecutive_stops = 0


def _open_from_signal(state: YTState, sig: Signal, px_open: float, ts: pd.Timestamp) -> None:
    fill_px = _slip(px_open, sig.side, opening=True)
    # 손절·익절이 fill_px 기준으로 잘못된 방향이면 거래 무효
    if sig.side == "LONG":
        if sig.stop_price >= fill_px or sig.tp_price <= fill_px:
            return
    else:
        if sig.stop_price <= fill_px or sig.tp_price >= fill_px:
            return
    notional = state.equity * POSITION_PCT
    fee = TAKER_FEE * notional
    state.equity -= fee
    state.total_fees += fee
    state.position = YTTrade(
        side=sig.side,
        entry_time=ts,
        entry_price=fill_px,
        notional=notional,
        stop_price=sig.stop_price,
        tp_price=sig.tp_price,
        tp2_price=sig.tp2_price,
    )


def _apply_funding(state: YTState, row: pd.Series) -> None:
    pos = state.position
    if pos is None:
        return
    ts = row["open_time"]
    if ts.hour in FUNDING_HOURS_UTC and ts.minute == 0:
        cost = FUNDING_RATE * pos.notional * pos.remaining_pct
        state.equity -= cost
        state.total_funding += cost


def _check_intrabar_exits(state: YTState, row: pd.Series, idx: int) -> None:
    pos = state.position
    if pos is None:
        return
    h, l = row["high"], row["low"]
    ts = row["close_time"]

    if pos.side == "LONG":
        sl_touch = l <= pos.stop_price
        tp1_touch = (not pos.tp1_done) and h >= pos.tp_price
        tp2_touch = (pos.tp2_price is not None) and h >= pos.tp2_price
    else:
        sl_touch = h >= pos.stop_price
        tp1_touch = (not pos.tp1_done) and l <= pos.tp_price
        tp2_touch = (pos.tp2_price is not None) and l <= pos.tp2_price

    if sl_touch:
        _close_remaining(state, pos.stop_price, "STOP_LOSS", ts, idx)
        return

    if pos.tp2_price is None:
        # 단일 익절: tp 닿으면 전량 청산
        if tp1_touch:
            _close_remaining(state, pos.tp_price, "TP", ts, idx)
        return

    # 2단 익절: tp 에서 50%, tp2 에서 나머지 50%
    if tp1_touch:
        _close_partial(state, pos.tp_price, 0.5, "TP1", ts)
        pos.tp1_done = True
        # 손익보호: 1차 도달 후 손절을 진입가로 이동 (BE)
        pos.stop_price = pos.entry_price
    if tp2_touch:
        _close_remaining(state, pos.tp2_price, "TP2", ts, idx)


def run_yt_backtest(df: pd.DataFrame, hook: StrategyHook) -> YTState:
    """단일 전략을 df 전체에 적용. df 는 hook.prepare() 결과여야 함."""
    state = YTState()
    df = df.reset_index(drop=True)
    n = len(df)
    for i in range(n):
        row = df.iloc[i]
        ts = row["close_time"]
        _reset_daily_if_needed(state, ts)

        # 1) 펜딩 신호 실행 (이 캔들 OPEN)
        if state.pending_signal is not None and state.position is None:
            if not state.halt_today and (i - state.last_exit_idx) > COOLDOWN_CANDLES:
                _open_from_signal(state, state.pending_signal, row["open"], ts)
            state.pending_signal = None

        # 2) 펀딩
        _apply_funding(state, row)

        # 3) 인트라바 SL/TP
        _check_intrabar_exits(state, row, i)

        # 4) 신호 평가 (close 시점) → 다음 봉으로 펜딩
        if state.position is None and not state.halt_today \
                and (i - state.last_exit_idx) > COOLDOWN_CANDLES:
            sig = hook.signal(df, i)
            if sig is not None:
                state.pending_signal = sig

        # 5) Equity & MDD
        state.peak_equity = max(state.peak_equity, state.equity)
        dd = (state.peak_equity - state.equity) / state.peak_equity
        state.max_drawdown = max(state.max_drawdown, dd)
        state.equity_curve.append((ts, state.equity))

    # 미청산 포지션은 마지막 종가에서 강제 청산
    if state.position is not None:
        last = df.iloc[-1]
        _close_remaining(state, last["close"], "FORCED_FINAL", last["close_time"], n - 1)
    return state


# ─────────────────────────────────────────────────────────────
# 기존 summarize() 와 호환되도록 closed_trades 인터페이스를 흉내내기
# ─────────────────────────────────────────────────────────────


def adapt_for_summary(state: YTState) -> object:
    """backtest.run.summarize() 는 closed_trades[i].fills[k][4]/[5] 만 본다.
    YTTrade 는 같은 형태이므로 그대로 호환된다. equity/MDD 도 직접 동일.
    """
    return state
