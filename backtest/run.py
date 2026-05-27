"""백테스트 엔진 v0.1 — 전략 규칙 v0.1 검증용.

명세서 02_backtest_spec.md 와 설계서 trading_bot_design.md 의 다음 조항을
반영한다:

- 신호 발생 캔들의 **다음 캔들 시가로 진입** (look-ahead 방지, 명세서 6.1)
- 손절은 거래소측 STOP_MARKET 가정: 인트라바 캔들 고저가가 손절가를 관통하면
  손절 체결로 시뮬레이션 (명세서 4)
- 손절·익절이 같은 캔들에서 모두 닿으면 **손절이 먼저 체결된 것으로 가정**
  (명세서 4 — 봉 내부 순서를 알 수 없으므로 보수적)
- 부분익절: 1R 30%, 2R 30%, 나머지는 CCI 0선 반대돌파 (설계서 2.7)
- 비용 모델: 바이낸스 선물 taker 수수료, 슬리피지, 펀딩 (명세서 5)
- in-sample 70% / out-of-sample 30% 분할 평가 (명세서 6.2)
- 거래소 손절은 1캔들마다 봇이 감시하는 동적 익절·반대신호와 분리됨
  (설계서 2.7 손절·익절 분담 원칙)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# 프로젝트 루트를 sys.path 에 추가해 strategy/ 를 import.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from strategy.indicators import adx, cci, ema, rsi  # noqa: E402
from strategy.rules import (  # noqa: E402
    IndicatorSnapshot,
    StrategyParams,
    cci_zero_cross_exit,
    long_entry_signal,
    short_entry_signal,
)


# ─────────────────────────────────────────────────────────────
# 설정 (백테스트 명세서 5절 — 보수적으로)
# ─────────────────────────────────────────────────────────────

DATA_DIR = PROJECT_ROOT / "data"
RESULTS_DIR = DATA_DIR / "backtest_results"

INITIAL_EQUITY = 10_000.0          # USDT, 임의 출발 자본
RISK_PCT = 0.01                    # 1회 거래 허용 손실 비율 (설계서 3절)
STOP_PCT = 0.01                    # 손절 폭 = 진입가의 1.0% (설계서 2.7)
# position_pct = RISK / STOP = 1.0 (시드 100% 명목)
POSITION_PCT = RISK_PCT / STOP_PCT

TAKER_FEE = 0.0004                 # 바이낸스 USDT-M 선물 taker 0.04%
SLIPPAGE = 0.0002                  # 시장가 보수적 슬리피지 2 bp
FUNDING_RATE = 0.0001              # 1회 펀딩 0.01% (실제 데이터 없으면 보수)
# 펀딩 시각: 매일 00, 08, 16 UTC (바이낸스 표준)
FUNDING_HOURS_UTC = (0, 8, 16)

DAILY_LOSS_HALT_PCT = 0.03         # 일일 -3% 도달 시 그날 중단 (설계서 3절)
CONSECUTIVE_STOP_HALT = 2          # 2회 연속 손절 시 중단 (설계서 3절)
COOLDOWN_CANDLES = 1               # 청산 후 최소 1캔들 대기 (설계서 4절)

IN_SAMPLE_RATIO = 0.7              # IS 70% / OOS 30% (명세서 6.2)


# ─────────────────────────────────────────────────────────────
# 데이터 로딩
# ─────────────────────────────────────────────────────────────


def load_klines(path: Path) -> pd.DataFrame:
    """download_binance_data.py 가 만든 CSV 를 로딩 — open_time 은 ms 단위 UTC."""
    df = pd.read_csv(path)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col])
    df = df.sort_values("open_time").reset_index(drop=True)
    return df


def prepare_15m_with_indicators(
    df15: pd.DataFrame,
    df1h: pd.DataFrame,
    params: StrategyParams,
) -> pd.DataFrame:
    """15분봉에 ADX/CCI/RSI 및 1시간봉 EMA50(시간 기반 forward-fill) 결합."""
    out = df15.copy()
    out["adx"] = adx(out["high"], out["low"], out["close"], period=14)
    out["cci"] = cci(out["high"], out["low"], out["close"], period=20)
    out["cci_prev"] = out["cci"].shift(1)
    out["rsi"] = rsi(out["close"], period=14)

    # 1h EMA50 — 1h 데이터에서 계산 후 close_time 기준으로 15m 에 backward-merge.
    # 결과: 각 15m 캔들의 close_time 이하인 가장 최근 1h close_time 의 EMA50.
    h = df1h[["close_time", "close"]].copy()
    h["ema50_1h"] = ema(h["close"], 50)
    h = h.rename(columns={"close_time": "ref_time"})

    out = pd.merge_asof(
        out.sort_values("close_time"),
        h[["ref_time", "ema50_1h"]].sort_values("ref_time"),
        left_on="close_time",
        right_on="ref_time",
        direction="backward",
    ).drop(columns=["ref_time"])
    return out


# ─────────────────────────────────────────────────────────────
# 백테스트 상태 & 엔진
# ─────────────────────────────────────────────────────────────


@dataclass
class Trade:
    side: str                      # "LONG" | "SHORT"
    entry_time: pd.Timestamp
    entry_price: float             # 슬리피지·수수료 적용된 유효 진입가
    notional: float                # 진입 시점 명목 (USDT)
    stop_price: float
    tp1_price: float
    tp2_price: float
    tp1_done: bool = False
    tp2_done: bool = False
    remaining_pct: float = 1.0     # 초기 사이즈 대비 남은 비율
    fills: list = field(default_factory=list)
    # fills 항목: (exit_time, exit_price_eff, size_pct, reason, gross_pnl, fee, funding)


@dataclass
class EngineState:
    equity: float = INITIAL_EQUITY
    peak_equity: float = INITIAL_EQUITY
    max_drawdown: float = 0.0
    daily_pnl_pct: float = 0.0
    daily_anchor_equity: float = INITIAL_EQUITY
    current_utc_day: Optional[pd.Timestamp] = None
    consecutive_stops: int = 0
    halt_today: bool = False
    last_exit_idx: int = -10_000   # 쿨다운 카운터
    position: Optional[Trade] = None
    pending_exit_reason: Optional[str] = None
    pending_entry_side: Optional[str] = None
    closed_trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
    total_fees: float = 0.0
    total_funding: float = 0.0


def _slip(price: float, side: str, opening: bool) -> float:
    """진입·청산 양쪽에 보수적으로 불리한 방향 슬리피지 적용."""
    if (side == "LONG" and opening) or (side == "SHORT" and not opening):
        return price * (1.0 + SLIPPAGE)
    return price * (1.0 - SLIPPAGE)


def _funding_count_between(prev_time: pd.Timestamp, this_time: pd.Timestamp) -> int:
    """(prev_time, this_time] 구간 안의 펀딩 시각 개수."""
    if pd.isna(prev_time) or this_time <= prev_time:
        return 0
    # 시작 직후~끝까지의 시점에서 hour ∈ FUNDING_HOURS_UTC 이고 minute == 0 인 곳을 센다.
    # 15m 그리드 위에서는 hour 가 FUNDING_HOURS 에 속하고 minute == 0 인 캔들 1개로 표현됨.
    return int(this_time.hour in FUNDING_HOURS_UTC and this_time.minute == 0)


def _reset_daily_if_needed(state: EngineState, ts: pd.Timestamp) -> None:
    day = ts.normalize()
    if state.current_utc_day is None or day != state.current_utc_day:
        state.current_utc_day = day
        state.daily_pnl_pct = 0.0
        state.daily_anchor_equity = state.equity
        state.halt_today = False
        # consecutive_stops 는 명세상 그날 단위가 명확하지 않으나, 일일 한도와
        # 같이 매일 리셋하는 것이 일반적이라 같이 리셋.
        state.consecutive_stops = 0


def _close_partial(
    state: EngineState,
    px: float,
    size_pct: float,
    reason: str,
    ts: pd.Timestamp,
) -> None:
    """포지션의 size_pct (초기 대비) 만큼 px 에서 청산."""
    pos = state.position
    assert pos is not None
    direction = 1 if pos.side == "LONG" else -1
    fill_px = _slip(px, pos.side, opening=False)
    fill_size_usdt = pos.notional * size_pct
    gross_pnl = direction * (fill_px - pos.entry_price) / pos.entry_price * fill_size_usdt
    fee = TAKER_FEE * fill_size_usdt
    state.equity += gross_pnl - fee
    state.total_fees += fee
    pos.fills.append((ts, fill_px, size_pct, reason, gross_pnl, fee, 0.0))
    pos.remaining_pct -= size_pct
    # 일일 손익 갱신
    state.daily_pnl_pct = (state.equity - state.daily_anchor_equity) / state.daily_anchor_equity
    if state.daily_pnl_pct <= -DAILY_LOSS_HALT_PCT:
        state.halt_today = True


def _close_remaining(
    state: EngineState,
    px: float,
    reason: str,
    ts: pd.Timestamp,
    idx: int,
) -> None:
    pos = state.position
    assert pos is not None
    if pos.remaining_pct > 1e-9:
        _close_partial(state, px, pos.remaining_pct, reason, ts)
    # 트레이드 종결
    state.closed_trades.append(pos)
    state.position = None
    state.last_exit_idx = idx
    # 연속 손절 카운트
    if reason == "STOP_LOSS":
        state.consecutive_stops += 1
        if state.consecutive_stops >= CONSECUTIVE_STOP_HALT:
            state.halt_today = True
    else:
        state.consecutive_stops = 0


def _open_position(
    state: EngineState,
    side: str,
    px_open: float,
    ts: pd.Timestamp,
    params: StrategyParams,
) -> None:
    fill_px = _slip(px_open, side, opening=True)
    notional = state.equity * POSITION_PCT
    fee = TAKER_FEE * notional
    state.equity -= fee
    state.total_fees += fee
    stop = (
        fill_px * (1.0 - params.stop_loss_pct)
        if side == "LONG"
        else fill_px * (1.0 + params.stop_loss_pct)
    )
    r1 = params.stop_loss_pct * params.tp1_r_multiple
    r2 = params.stop_loss_pct * params.tp2_r_multiple
    tp1 = fill_px * (1.0 + r1) if side == "LONG" else fill_px * (1.0 - r1)
    tp2 = fill_px * (1.0 + r2) if side == "LONG" else fill_px * (1.0 - r2)
    state.position = Trade(
        side=side,
        entry_time=ts,
        entry_price=fill_px,
        notional=notional,
        stop_price=stop,
        tp1_price=tp1,
        tp2_price=tp2,
    )


def _apply_funding(state: EngineState, row: pd.Series) -> None:
    """캔들의 open_time 이 펀딩 시각(00/08/16:00 UTC)이면 펀딩 비용 가산.

    바이낸스 15분봉 close_time 은 항상 :59:59.999 라서 close 기준으로는
    펀딩 시각을 절대 잡지 못한다. 진입 시점의 open_time 이 정시 펀딩과
    일치하는 첫 캔들에서 비용을 부과한다 (보수적으로 항상 불리한 방향).
    """
    pos = state.position
    if pos is None:
        return
    ts = row["open_time"]
    if ts.hour in FUNDING_HOURS_UTC and ts.minute == 0:
        cost = FUNDING_RATE * pos.notional * pos.remaining_pct
        state.equity -= cost
        state.total_funding += cost


def _check_intrabar_exits(state: EngineState, row: pd.Series, idx: int) -> bool:
    """캔들 H/L 로 SL/TP 트리거를 확인. 청산 발생 시 True."""
    pos = state.position
    if pos is None:
        return False
    h, l = row["high"], row["low"]
    ts = row["close_time"]

    if pos.side == "LONG":
        sl_touch = l <= pos.stop_price
        tp1_touch = (not pos.tp1_done) and h >= pos.tp1_price
        tp2_touch = (not pos.tp2_done) and h >= pos.tp2_price
    else:
        sl_touch = h >= pos.stop_price
        tp1_touch = (not pos.tp1_done) and l <= pos.tp1_price
        tp2_touch = (not pos.tp2_done) and l <= pos.tp2_price

    if sl_touch:
        # 명세 4절: SL 과 TP 가 같은 캔들에서 동시에 닿으면 SL 우선.
        _close_remaining(state, pos.stop_price, "STOP_LOSS", ts, idx)
        return True

    if tp1_touch:
        _close_partial(state, pos.tp1_price, 0.3, "TP1_1R", ts)
        pos.tp1_done = True
    if tp2_touch:
        _close_partial(state, pos.tp2_price, 0.3, "TP2_2R", ts)
        pos.tp2_done = True
    if pos.remaining_pct <= 1e-9:
        # 전부 익절된 케이스 (TP1 + TP2 = 0.6; 보통 안 일어남, 안전망)
        state.closed_trades.append(pos)
        state.position = None
        state.last_exit_idx = idx
        state.consecutive_stops = 0
        return True
    return False


def run_backtest(df: pd.DataFrame, params: StrategyParams) -> EngineState:
    """단일 시퀀스에 대해 전 기간 시뮬레이션."""
    state = EngineState()
    df = df.reset_index(drop=True)
    n = len(df)

    for i in range(n):
        row = df.iloc[i]
        ts = row["close_time"]

        # 신뢰 가능한 지표가 안 잡힌 구간은 건너뜀.
        if pd.isna(row["adx"]) or pd.isna(row["cci"]) or pd.isna(row["cci_prev"]) \
                or pd.isna(row["rsi"]) or pd.isna(row["ema50_1h"]):
            state.equity_curve.append((ts, state.equity))
            continue

        _reset_daily_if_needed(state, ts)

        # 1) 이전 캔들 종가 신호에 따른 펜딩 실행 (이 캔들의 OPEN 에서 체결)
        if state.pending_exit_reason is not None and state.position is not None:
            _close_remaining(state, row["open"], state.pending_exit_reason, ts, i)
            state.pending_exit_reason = None
        if state.pending_entry_side is not None and state.position is None:
            if not state.halt_today and (i - state.last_exit_idx) > COOLDOWN_CANDLES:
                _open_position(state, state.pending_entry_side, row["open"], ts, params)
            state.pending_entry_side = None

        # 2) 펀딩 비용 적용 (해당 시각이면)
        _apply_funding(state, row)

        # 3) 인트라바 SL/TP
        _check_intrabar_exits(state, row, i)

        # 4) 캔들 종가에서 신호 평가 → 다음 캔들 OPEN 에서 실행하도록 펜딩
        snap = IndicatorSnapshot(
            adx=float(row["adx"]),
            cci_now=float(row["cci"]),
            cci_prev=float(row["cci_prev"]),
            rsi=float(row["rsi"]),
            close_15m=float(row["close"]),
            ema50_1h=float(row["ema50_1h"]),
        )
        if state.position is not None:
            pos = state.position
            # 청산 트리거: CCI 0선 반대돌파 또는 반대 방향 진입 신호
            if cci_zero_cross_exit(pos.side, snap.cci_now, snap.cci_prev):
                state.pending_exit_reason = "CCI_ZERO_CROSS"
            elif pos.side == "LONG" and short_entry_signal(snap, params):
                state.pending_exit_reason = "OPPOSITE_SIGNAL"
            elif pos.side == "SHORT" and long_entry_signal(snap, params):
                state.pending_exit_reason = "OPPOSITE_SIGNAL"
        else:
            if not state.halt_today and (i - state.last_exit_idx) > COOLDOWN_CANDLES:
                if long_entry_signal(snap, params):
                    state.pending_entry_side = "LONG"
                elif short_entry_signal(snap, params):
                    state.pending_entry_side = "SHORT"

        # 5) Equity & MDD 갱신
        state.peak_equity = max(state.peak_equity, state.equity)
        dd = (state.peak_equity - state.equity) / state.peak_equity
        state.max_drawdown = max(state.max_drawdown, dd)
        state.equity_curve.append((ts, state.equity))

    # 백테스트 끝났는데 포지션 남았으면 마지막 종가로 청산.
    if state.position is not None:
        last = df.iloc[-1]
        _close_remaining(state, last["close"], "FORCED_FINAL", last["close_time"], n - 1)

    return state


# ─────────────────────────────────────────────────────────────
# 리포트
# ─────────────────────────────────────────────────────────────


def trade_net_pnl(t: Trade) -> float:
    return sum(f[4] - f[5] for f in t.fills)


def summarize(state: EngineState, label: str, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    trades = state.closed_trades
    n = len(trades)
    pnls = [trade_net_pnl(t) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total_pnl = state.equity - INITIAL_EQUITY
    total_return = total_pnl / INITIAL_EQUITY

    win_rate = len(wins) / n if n else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    profit_factor = (sum(wins) / -sum(losses)) if losses else float("inf")

    # 연속 손실 최대치
    max_consec_loss = 0
    cur = 0
    for p in pnls:
        if p < 0:
            cur += 1
            max_consec_loss = max(max_consec_loss, cur)
        else:
            cur = 0

    return {
        "label": label,
        "period": f"{start.date()} ~ {end.date()}",
        "trades": n,
        "total_return_pct": total_return * 100,
        "ending_equity": state.equity,
        "max_drawdown_pct": state.max_drawdown * 100,
        "win_rate_pct": win_rate * 100,
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "total_fees": state.total_fees,
        "total_funding": state.total_funding,
        "max_consecutive_losses": max_consec_loss,
    }


def print_report(r: dict) -> None:
    pf = r["profit_factor"]
    pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
    print(f"\n── {r['label']}  ({r['period']}) ──")
    print(f"  거래 횟수:              {r['trades']}")
    print(f"  총 수익률:              {r['total_return_pct']:+.2f}%")
    print(f"  종료 자본 (시드 {INITIAL_EQUITY:,.0f}):  {r['ending_equity']:,.2f} USDT")
    print(f"  최대 낙폭 (MDD):        -{r['max_drawdown_pct']:.2f}%")
    print(f"  승률:                   {r['win_rate_pct']:.1f}%")
    print(f"  손익비 (profit factor): {pf_str}")
    print(f"  평균 수익 / 평균 손실:  {r['avg_win']:+.2f} / {r['avg_loss']:+.2f}")
    print(f"  연속 손실 최대:         {r['max_consecutive_losses']}")
    print(f"  총 수수료:              {r['total_fees']:,.2f} USDT")
    print(f"  총 펀딩 비용:           {r['total_funding']:,.2f} USDT")


def dump_artifacts(state: EngineState, suffix: str) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    eq = pd.DataFrame(state.equity_curve, columns=["time", "equity"])
    eq.to_csv(RESULTS_DIR / f"equity_{suffix}.csv", index=False)

    rows = []
    for t in state.closed_trades:
        for (ts, px, sz, reason, gross, fee, _funding) in t.fills:
            rows.append({
                "entry_time": t.entry_time,
                "side": t.side,
                "entry_price": t.entry_price,
                "exit_time": ts,
                "exit_price": px,
                "size_pct_of_initial": sz,
                "reason": reason,
                "gross_pnl": gross,
                "fee": fee,
            })
    pd.DataFrame(rows).to_csv(RESULTS_DIR / f"fills_{suffix}.csv", index=False)


# ─────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────


def main() -> None:
    # Windows PowerShell cp949 호환 출력
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")

    df15 = load_klines(DATA_DIR / "BTCUSDT-15m.csv")
    df1h = load_klines(DATA_DIR / "BTCUSDT-1h.csv")
    print(f"15m 캔들 {len(df15):,}개  ({df15['close_time'].iloc[0]} ~ {df15['close_time'].iloc[-1]})")
    print(f"1h  캔들 {len(df1h):,}개")

    params = StrategyParams()
    prepared = prepare_15m_with_indicators(df15, df1h, params)

    # IS/OOS 분할 — 시간순으로 70:30
    split = int(len(prepared) * IN_SAMPLE_RATIO)
    is_df = prepared.iloc[:split].reset_index(drop=True)
    oos_df = prepared.iloc[split:].reset_index(drop=True)

    print(f"\nIn-sample:  {is_df['close_time'].iloc[0]} ~ {is_df['close_time'].iloc[-1]}  ({len(is_df):,}봉)")
    print(f"Out-of-sample: {oos_df['close_time'].iloc[0]} ~ {oos_df['close_time'].iloc[-1]}  ({len(oos_df):,}봉)")

    print("\n[IS] 시뮬레이션 진행 중...")
    is_state = run_backtest(is_df, params)
    is_report = summarize(is_state, "IN-SAMPLE", is_df["close_time"].iloc[0], is_df["close_time"].iloc[-1])
    print_report(is_report)
    dump_artifacts(is_state, "is")

    print("\n[OOS] 시뮬레이션 진행 중...")
    oos_state = run_backtest(oos_df, params)
    oos_report = summarize(oos_state, "OUT-OF-SAMPLE", oos_df["close_time"].iloc[0], oos_df["close_time"].iloc[-1])
    print_report(oos_report)
    dump_artifacts(oos_state, "oos")

    print(f"\n산출물: {RESULTS_DIR}")
    print("  equity_{is,oos}.csv  — 시점별 자본곡선")
    print("  fills_{is,oos}.csv   — 진입·청산 체결 로그 (부분익절은 여러 행)")


if __name__ == "__main__":
    main()
