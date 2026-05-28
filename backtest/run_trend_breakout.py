"""Trend Breakout — IS-only 백테스트 + 레버리지 1/2/3 sweep.

15m 데이터 IS 70% 구간만 사용. OOS 는 §6.2 보호 (BTC 데이터셋에서 이미 2회 사용).

실행:
    python backtest/run_trend_breakout.py

선택 인자:
    --leverages 1,2,3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402

from backtest.run import (  # noqa: E402
    DATA_DIR,
    INITIAL_EQUITY,
    IN_SAMPLE_RATIO,
    load_klines,
)
from backtest.trend_breakout_engine import TBState, run_trend_breakout  # noqa: E402
from strategy.trend_breakout import (  # noqa: E402
    TBParams,
    merge_tf,
    prepare_15m,
    prepare_1h,
)


RESULTS_DIR = DATA_DIR / "backtest_results"


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leverages", default="1,2,3",
                    help="comma-separated leverages, e.g. '1,2,3'")
    return ap.parse_args()


def summarize_tb(state: TBState, label: str, start_ts: pd.Timestamp,
                 end_ts: pd.Timestamp) -> dict:
    closed = state.closed_positions
    n = len(closed)
    pnls = [pos.realized_pnl for pos in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total_pnl = state.equity - INITIAL_EQUITY
    total_ret = total_pnl / INITIAL_EQUITY

    win_rate = len(wins) / n if n else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    pf = (sum(wins) / -sum(losses)) if losses else float("inf")

    # 연속 손실
    max_consec = 0
    cur = 0
    for p in pnls:
        if p < 0:
            cur += 1
            max_consec = max(max_consec, cur)
        else:
            cur = 0

    # 사이드별
    longs = [pos for pos in closed if pos.side == "LONG"]
    shorts = [pos for pos in closed if pos.side == "SHORT"]
    long_wins = sum(1 for pos in longs if pos.realized_pnl > 0)
    short_wins = sum(1 for pos in shorts if pos.realized_pnl > 0)
    long_pnl = sum(pos.realized_pnl for pos in longs)
    short_pnl = sum(pos.realized_pnl for pos in shorts)

    # 종료 사유 분포
    reasons: dict[str, int] = {}
    for pos in closed:
        reasons[pos.close_reason or "?"] = reasons.get(pos.close_reason or "?", 0) + 1
    liq_count = reasons.get("LIQUIDATED", 0)

    # 평균 손절폭, 평균 보유봉 수
    avg_sl_pct = (sum(pos.sl_pct for pos in closed) / n) if n else 0.0
    durations = [(pos.close_idx - pos.entry_idx) for pos in closed
                 if pos.close_idx is not None]
    avg_dur_bars = (sum(durations) / len(durations)) if durations else 0.0

    return {
        "label": label,
        "period": f"{start_ts.date()} ~ {end_ts.date()}",
        "trades": n,
        "total_return_pct": total_ret * 100,
        "ending_equity": state.equity,
        "max_drawdown_pct": state.max_drawdown * 100,
        "win_rate_pct": win_rate * 100,
        "profit_factor": pf,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "max_consecutive_losses": max_consec,
        "total_fees": state.total_fees,
        "total_funding": state.total_funding,
        "longs": len(longs),
        "long_wins": long_wins,
        "long_pnl": long_pnl,
        "shorts": len(shorts),
        "short_wins": short_wins,
        "short_pnl": short_pnl,
        "liq_count": liq_count,
        "reasons": reasons,
        "avg_sl_pct": avg_sl_pct,
        "avg_dur_bars_15m": avg_dur_bars,
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
    print(f"  평균 손절폭:            {r['avg_sl_pct']*100:.2f}%")
    print(f"  평균 보유 (15m봉):      {r['avg_dur_bars_15m']:.1f} 봉")
    print(f"  강제청산 횟수:          {r['liq_count']}")
    print(f"  롱:  {r['longs']:>4} (승 {r['long_wins']})  PnL {r['long_pnl']:+,.2f}")
    print(f"  숏:  {r['shorts']:>4} (승 {r['short_wins']})  PnL {r['short_pnl']:+,.2f}")
    print(f"  종료사유 분포:          {r['reasons']}")
    print(f"  총 수수료/펀딩:         {r['total_fees']:,.2f} / {r['total_funding']:,.2f} USDT")


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    leverages = [float(x.strip()) for x in args.leverages.split(",") if x.strip()]

    # 데이터
    df15 = load_klines(DATA_DIR / "BTCUSDT-15m.csv")
    df1h = load_klines(DATA_DIR / "BTCUSDT-1h.csv")
    print(f"15m {len(df15):,}봉  ({df15['close_time'].iloc[0]} ~ {df15['close_time'].iloc[-1]})")
    print(f"1h  {len(df1h):,}봉")

    # IS / OOS 분할 — 15m 기준
    split = int(len(df15) * IN_SAMPLE_RATIO)
    is_df_15m = df15.iloc[:split].reset_index(drop=True)
    is_start_t = is_df_15m["close_time"].iloc[0]
    is_end_t = is_df_15m["close_time"].iloc[-1]
    print(f"IS: {is_start_t} ~ {is_end_t}  ({len(is_df_15m):,}봉)")
    # 1h 도 같은 시간 구간으로 자름
    is_df_1h = df1h[df1h["close_time"] <= is_end_t].reset_index(drop=True)
    print(f"IS 1h: {len(is_df_1h):,}봉\n")
    print("(OOS 는 §6.2 보호 — 이 데이터셋에서 이미 2회 사용. 건드리지 않음)\n")

    # 레버리지별 백테스트
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    headline = []
    for lev in leverages:
        p = TBParams(leverage=lev)
        prepped_15m = prepare_15m(is_df_15m, p)
        prepped_1h = prepare_1h(is_df_1h, p)
        merged = merge_tf(prepped_15m, prepped_1h)

        state = run_trend_breakout(merged, p)
        rep = summarize_tb(state, f"TB lev{int(lev) if lev.is_integer() else lev}",
                           is_start_t, is_end_t)
        print_report(rep)

        # 아티팩트
        eq = pd.DataFrame(state.equity_curve, columns=["time", "equity"])
        eq.to_csv(RESULTS_DIR / f"equity_tb_lev{int(lev)}_is.csv", index=False)
        fills_rows = []
        for pos in state.closed_positions:
            for ex in pos.exits:
                ts, px, qty, reason, gross, fee = ex
                fills_rows.append({
                    "side": pos.side,
                    "entry_time": pos.entry_ts,
                    "entry_price": pos.entry_price,
                    "exit_time": ts,
                    "exit_price": px,
                    "qty": qty,
                    "reason": reason,
                    "gross_pnl": gross,
                    "fee": fee,
                })
        pd.DataFrame(fills_rows).to_csv(
            RESULTS_DIR / f"fills_tb_lev{int(lev)}_is.csv", index=False
        )

        headline.append((lev, rep))

    # 비교표
    print("\n\n========== 레버리지 비교 (IS) ==========")
    print(f"{'lev':>4}  {'거래':>5}  {'수익률':>9}  {'MDD':>7}  "
          f"{'PF':>6}  {'승률':>5}  {'청산':>4}")
    print("-" * 60)
    for lev, r in headline:
        pf = r["profit_factor"]
        pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(
            f"{lev:>4.0f}x {r['trades']:>5}  {r['total_return_pct']:>+8.2f}%  "
            f"-{r['max_drawdown_pct']:>5.2f}%  {pf_str:>6}  "
            f"{r['win_rate_pct']:>4.1f}%  {r['liq_count']:>4}"
        )


if __name__ == "__main__":
    main()
