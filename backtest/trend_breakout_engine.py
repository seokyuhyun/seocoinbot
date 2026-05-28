"""Trend Breakout 엔진 — 단일 진입 + 3단 분할익절 + 리스크 기반 사이즈.

전략 명세는 strategy/trend_breakout.py 참조.

봉 처리 순서 (per 15m 봉):
  Stage 1: pending signal 체결 (이 봉 OPEN)
  Stage 2: 펀딩 (00/08/16 UTC 정시 봉)
  Stage 3: 인트라바 SL/TP/청산 — 우선순위: liq > SL > TP. 같은 봉 SL/TP → SL 우선
  Stage 4: 봉 종가 신호 평가 → 다음 봉 OPEN 펜딩
  Stage 5: equity / MDD (mark-to-market)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.run import (  # noqa: E402
    COOLDOWN_CANDLES,
    CONSECUTIVE_STOP_HALT,
    DAILY_LOSS_HALT_PCT,
    FUNDING_HOURS_UTC,
    FUNDING_RATE,
    INITIAL_EQUITY,
    SLIPPAGE,
    TAKER_FEE,
)
from strategy.trend_breakout import TBParams, long_signal, short_signal  # noqa: E402


# 강제청산 수수료 (Binance USDT-M ~0.05%)
LIQUIDATION_FEE = 0.0005


@dataclass
class TBPosition:
    side: str
    entry_idx: int
    entry_ts: pd.Timestamp
    entry_price: float
    notional: float
    margin: float
    leverage: float
    qty_original: float
    qty_remaining: float
    stop_price: float
    sl_pct: float
    tp_prices: list  # [tp1, tp2, tp3]
    tp_fractions: tuple = (0.40, 0.30, 0.30)
    tp_done: list = field(default_factory=lambda: [False, False, False])
    breakeven_active: bool = False
    liq_price: float = 0.0
    realized_pnl: float = 0.0
    fee_paid: float = 0.0
    funding_paid: float = 0.0
    close_reason: Optional[str] = None
    close_ts: Optional[pd.Timestamp] = None
    close_idx: Optional[int] = None
    exits: list = field(default_factory=list)  # (ts, px, qty, reason, gross, fee)


@dataclass
class TBState:
    equity: float = INITIAL_EQUITY
    peak_equity: float = INITIAL_EQUITY
    max_drawdown: float = 0.0
    daily_pnl_pct: float = 0.0
    daily_anchor_equity: float = INITIAL_EQUITY
    current_utc_day: Optional[pd.Timestamp] = None
    consecutive_stops: int = 0
    halt_today: bool = False
    last_exit_idx: int = -10_000
    position: Optional[TBPosition] = None
    pending_signal: Optional[dict] = None
    closed_positions: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
    total_fees: float = 0.0
    total_funding: float = 0.0


def _slip(px: float, side: str, opening: bool) -> float:
    if (side == "LONG" and opening) or (side == "SHORT" and not opening):
        return px * (1.0 + SLIPPAGE)
    return px * (1.0 - SLIPPAGE)


def _liq_price(side: str, avg: float, leverage: float, mmr: float) -> float:
    if side == "LONG":
        return avg * (1.0 - 1.0 / leverage + mmr)
    return avg * (1.0 + 1.0 / leverage - mmr)


def _reset_daily(state: TBState, ts: pd.Timestamp) -> None:
    day = ts.normalize()
    if state.current_utc_day is None or day != state.current_utc_day:
        state.current_utc_day = day
        state.daily_pnl_pct = 0.0
        state.daily_anchor_equity = state.equity
        state.halt_today = False
        state.consecutive_stops = 0


def _open(state: TBState, sig: dict, px_open: float, ts: pd.Timestamp,
          idx: int, p: TBParams) -> None:
    side = sig["side"]
    fill = _slip(px_open, side, opening=True)

    # signal 시점 stop_ref 는 close 기준. fill 기준으로 SL 재정렬.
    # 단순화: stop_price 는 그대로 유지 (절대가격이므로).
    stop = sig["stop_ref"]
    if side == "LONG":
        if stop >= fill:
            return
        sl_pct_fill = (fill - stop) / fill
    else:
        if stop <= fill:
            return
        sl_pct_fill = (stop - fill) / fill

    # fill 기준 clamp 재검증
    if sl_pct_fill > p.sl_max_pct:
        return
    if sl_pct_fill < p.sl_min_pct:
        # 0.4% 강제 적용
        if side == "LONG":
            stop = fill * (1.0 - p.sl_min_pct)
        else:
            stop = fill * (1.0 + p.sl_min_pct)
        sl_pct_fill = p.sl_min_pct

    # 리스크 기반 notional
    risk_usdt = state.equity * p.risk_per_trade_pct
    notional = risk_usdt / sl_pct_fill
    max_notional = state.equity * p.leverage      # 마진 부족 방지
    if notional > max_notional:
        notional = max_notional
    margin = notional / p.leverage
    qty = notional / fill

    fee = TAKER_FEE * notional
    state.equity -= fee
    state.total_fees += fee

    # TPs (R-based)
    if side == "LONG":
        tps = [fill + (fill - stop) * r for r in p.tp_r_multiples]
    else:
        tps = [fill - (stop - fill) * r for r in p.tp_r_multiples]

    pos = TBPosition(
        side=side,
        entry_idx=idx,
        entry_ts=ts,
        entry_price=fill,
        notional=notional,
        margin=margin,
        leverage=p.leverage,
        qty_original=qty,
        qty_remaining=qty,
        stop_price=stop,
        sl_pct=sl_pct_fill,
        tp_prices=tps,
        tp_fractions=p.tp_fractions,
    )
    pos.liq_price = _liq_price(side, fill, p.leverage, p.maintenance_mmr)
    state.position = pos


def _close_partial(state: TBState, px: float, qty: float, reason: str,
                   ts: pd.Timestamp) -> None:
    pos = state.position
    assert pos is not None
    if qty > pos.qty_remaining + 1e-12:
        qty = pos.qty_remaining
    if qty <= 1e-12:
        return
    fill = _slip(px, pos.side, opening=False)
    direction = 1 if pos.side == "LONG" else -1
    gross = direction * (fill - pos.entry_price) * qty
    fee = TAKER_FEE * fill * qty
    state.equity += gross - fee
    state.total_fees += fee
    pos.realized_pnl += gross - fee
    pos.fee_paid += fee
    pos.qty_remaining -= qty
    pos.exits.append((ts, fill, qty, reason, gross, fee))
    # 일일 손익 갱신
    state.daily_pnl_pct = (state.equity - state.daily_anchor_equity) / state.daily_anchor_equity
    if state.daily_pnl_pct <= -DAILY_LOSS_HALT_PCT:
        state.halt_today = True


def _close_all(state: TBState, px: float, reason: str, ts: pd.Timestamp,
               idx: int) -> None:
    pos = state.position
    if pos is None or pos.qty_remaining <= 1e-12:
        return
    _close_partial(state, px, pos.qty_remaining, reason, ts)
    pos.close_reason = reason
    pos.close_ts = ts
    pos.close_idx = idx
    state.closed_positions.append(pos)
    state.position = None
    state.last_exit_idx = idx
    if reason in ("STOP_LOSS", "LIQUIDATED"):
        state.consecutive_stops += 1
        if state.consecutive_stops >= CONSECUTIVE_STOP_HALT:
            state.halt_today = True
    elif reason.startswith("TP") or reason == "BE_STOP":
        # BE stop 은 손실 아님 (0 또는 미세이익) 이지만 reset 은 안전쪽
        state.consecutive_stops = 0


def _force_liquidate(state: TBState, px: float, ts: pd.Timestamp, idx: int) -> None:
    """청산 수수료 0.05% 적용 + LIQUIDATED 사유로 close."""
    pos = state.position
    assert pos is not None
    qty = pos.qty_remaining
    if qty <= 1e-12:
        return
    direction = 1 if pos.side == "LONG" else -1
    gross = direction * (px - pos.entry_price) * qty
    fee = LIQUIDATION_FEE * px * qty
    state.equity += gross - fee
    state.total_fees += fee
    pos.realized_pnl += gross - fee
    pos.fee_paid += fee
    pos.qty_remaining = 0.0
    pos.exits.append((ts, px, qty, "LIQUIDATED", gross, fee))
    pos.close_reason = "LIQUIDATED"
    pos.close_ts = ts
    pos.close_idx = idx
    state.closed_positions.append(pos)
    state.position = None
    state.last_exit_idx = idx
    state.consecutive_stops += 1
    if state.consecutive_stops >= CONSECUTIVE_STOP_HALT:
        state.halt_today = True


def _apply_funding(state: TBState, row: pd.Series) -> None:
    pos = state.position
    if pos is None:
        return
    ts = row["open_time"]
    if ts.hour in FUNDING_HOURS_UTC and ts.minute == 0:
        notional_now = pos.qty_remaining * pos.entry_price
        cost = FUNDING_RATE * notional_now
        state.equity -= cost
        state.total_funding += cost
        pos.funding_paid += cost


def _check_intrabar(state: TBState, row: pd.Series, idx: int, p: TBParams) -> None:
    pos = state.position
    if pos is None:
        return
    h, l = float(row["high"]), float(row["low"])
    ts = row["close_time"]

    if pos.side == "LONG":
        # 우선순위: liq (더 낮은 가격 = 더 먼저 hit 가능성, 단 SL 보다 낮으면)
        # 보통 SL > liq (SL 이 entry 에 더 가깝다), 그럼 SL 먼저 fire.
        # 만약 liq > SL (즉 청산이 SL 보다 entry 에 더 가까움) → liq 먼저.
        liq_first = pos.liq_price > pos.stop_price and l <= pos.liq_price
        if liq_first:
            _force_liquidate(state, pos.liq_price, ts, idx)
            return
        # SL 체크 (BE 든 원래 hard 든 동일)
        if l <= pos.stop_price:
            reason = "BE_STOP" if pos.breakeven_active else "STOP_LOSS"
            _close_all(state, pos.stop_price, reason, ts, idx)
            return
        # TPs (1→2→3 순)
        for k in range(3):
            if pos.tp_done[k]:
                continue
            tp = pos.tp_prices[k]
            if h >= tp:
                close_qty = pos.qty_original * pos.tp_fractions[k]
                _close_partial(state, tp, close_qty, f"TP{k+1}", ts)
                pos.tp_done[k] = True
                if k == 0 and pos.qty_remaining > 1e-9:
                    pos.stop_price = pos.entry_price
                    pos.breakeven_active = True
                if pos.qty_remaining <= 1e-9:
                    pos.close_reason = "TP_ALL"
                    pos.close_ts = ts
                    pos.close_idx = idx
                    state.closed_positions.append(pos)
                    state.position = None
                    state.last_exit_idx = idx
                    state.consecutive_stops = 0
                    return
            else:
                break  # 위쪽 TP 안 닿으면 그 위는 더더욱 안 닿음
    else:  # SHORT
        liq_first = pos.liq_price < pos.stop_price and h >= pos.liq_price
        if liq_first:
            _force_liquidate(state, pos.liq_price, ts, idx)
            return
        if h >= pos.stop_price:
            reason = "BE_STOP" if pos.breakeven_active else "STOP_LOSS"
            _close_all(state, pos.stop_price, reason, ts, idx)
            return
        for k in range(3):
            if pos.tp_done[k]:
                continue
            tp = pos.tp_prices[k]
            if l <= tp:
                close_qty = pos.qty_original * pos.tp_fractions[k]
                _close_partial(state, tp, close_qty, f"TP{k+1}", ts)
                pos.tp_done[k] = True
                if k == 0 and pos.qty_remaining > 1e-9:
                    pos.stop_price = pos.entry_price
                    pos.breakeven_active = True
                if pos.qty_remaining <= 1e-9:
                    pos.close_reason = "TP_ALL"
                    pos.close_ts = ts
                    pos.close_idx = idx
                    state.closed_positions.append(pos)
                    state.position = None
                    state.last_exit_idx = idx
                    state.consecutive_stops = 0
                    return
            else:
                break


def run_trend_breakout(df_merged: pd.DataFrame, p: TBParams) -> TBState:
    """df_merged 는 strategy.trend_breakout.merge_tf() 결과여야 함."""
    state = TBState()
    df = df_merged.reset_index(drop=True)
    n = len(df)

    for i in range(n):
        row = df.iloc[i]
        ts = row["close_time"]
        _reset_daily(state, ts)

        # Stage 1: pending signal 체결
        if state.pending_signal is not None and state.position is None:
            if not state.halt_today and (i - state.last_exit_idx) > COOLDOWN_CANDLES:
                _open(state, state.pending_signal, float(row["open"]), ts, i, p)
            state.pending_signal = None

        # Stage 2: 펀딩
        _apply_funding(state, row)

        # Stage 3: 인트라바
        _check_intrabar(state, row, i, p)

        # Stage 4: 봉 종가 신호
        if (state.position is None and not state.halt_today
                and (i - state.last_exit_idx) > COOLDOWN_CANDLES):
            sig = long_signal(row, p) or short_signal(row, p)
            if sig is not None:
                state.pending_signal = sig

        # Stage 5: equity / MDD
        eq_eval = state.equity
        if state.position is not None:
            pos = state.position
            direction = 1 if pos.side == "LONG" else -1
            unreal = direction * (float(row["close"]) - pos.entry_price) * pos.qty_remaining
            eq_eval = state.equity + unreal
        state.peak_equity = max(state.peak_equity, eq_eval)
        if state.peak_equity > 0:
            dd = (state.peak_equity - eq_eval) / state.peak_equity
            state.max_drawdown = max(state.max_drawdown, dd)
        state.equity_curve.append((ts, eq_eval))

    # 끝났는데 포지션 남았으면 마지막 종가
    if state.position is not None:
        last = df.iloc[-1]
        _close_all(state, float(last["close"]), "FORCED_FINAL",
                   last["close_time"], n - 1)

    return state
