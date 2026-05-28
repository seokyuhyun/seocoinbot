"""유튜브 4전략 IS 비교 백테스트.

- IS 70% 구간만 평가 (OOS 는 후속 단일 검증을 위해 미사용 — 명세 §6.2).
- 1h 메인 타임프레임 (v0.1 에서 검증한 최선의 TF).
- 각 전략은 자기 prepare/signal 인터페이스로 신호를 만들고 yt_engine 이 시뮬레이션.
- 결과는 한 표로 나란히 출력 + 각 전략의 fills/equity CSV 저장.

실행:
    python backtest/compare_yt.py [--main-tf 1h]
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
    RESULTS_DIR,
    load_klines,
    summarize,
    print_report,
)
from backtest.yt_engine import run_yt_backtest  # noqa: E402
from strategy.yt_strategies import ALL_STRATEGIES  # noqa: E402


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="YT 4전략 IS 비교")
    ap.add_argument("--main-tf", default="1h",
                    help="메인 타임프레임 (data/BTCUSDT-<TF>.csv)")
    return ap.parse_args()


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    main_path = DATA_DIR / f"BTCUSDT-{args.main_tf}.csv"
    if not main_path.exists():
        print(f"필요 파일 없음: {main_path}", file=sys.stderr)
        sys.exit(2)

    df = load_klines(main_path)
    split = int(len(df) * IN_SAMPLE_RATIO)
    is_df = df.iloc[:split].reset_index(drop=True)
    print(f"메인 {args.main_tf}: {len(df):,}봉  "
          f"({df['close_time'].iloc[0]} ~ {df['close_time'].iloc[-1]})")
    print(f"IS 구간: {is_df['close_time'].iloc[0]} ~ {is_df['close_time'].iloc[-1]}  "
          f"({len(is_df):,}봉)")
    print(f"(OOS 는 평가 안 함 — 명세 §6.2 단일사용 보호)\n")

    rows = []
    for strat in ALL_STRATEGIES:
        prepared = strat.prepare(is_df)
        state = run_yt_backtest(prepared, strat)
        rep = summarize(
            state,
            f"{strat.name}",
            is_df["close_time"].iloc[0],
            is_df["close_time"].iloc[-1],
        )
        print_report(rep)

        # 결과 dump
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        eq = pd.DataFrame(state.equity_curve, columns=["time", "equity"])
        eq.to_csv(RESULTS_DIR / f"equity_yt_{strat.name.lower()}_is.csv", index=False)
        fr = []
        for t in state.closed_trades:
            for (ts, px, sz, reason, gross, fee, _f) in t.fills:
                fr.append({
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
        pd.DataFrame(fr).to_csv(
            RESULTS_DIR / f"fills_yt_{strat.name.lower()}_is.csv", index=False
        )
        rows.append(rep)

    # 비교 표
    print("\n\n========== 비교 표 (IS) ==========")
    hdr = f"{'전략':<12} {'거래':>5} {'수익률':>9} {'MDD':>7} {'PF':>6} {'승률':>6} {'연속손실':>5}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        pf = r["profit_factor"]
        pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(
            f"{r['label']:<12} "
            f"{r['trades']:>5} "
            f"{r['total_return_pct']:>+8.2f}% "
            f"{r['max_drawdown_pct']:>6.2f}% "
            f"{pf_s:>6} "
            f"{r['win_rate_pct']:>5.1f}% "
            f"{r['max_consecutive_losses']:>5}"
        )

    print("\n참고 — v0.1 IS (1h, CCI±130, trailing): 거래 67 / +6.74% / PF 1.29 / 승률 34.3%")
    print("→ OOS 에서 -9.99% / PF 0.48 로 무너졌음. 새 전략 중 IS 가 양호한 것을 OOS 검증.")


if __name__ == "__main__":
    main()
