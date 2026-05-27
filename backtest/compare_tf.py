"""타임프레임 별 IS 비교 — 1h main + 4h HTF에서 v0.1 / v0.2 변형 평가.

15m 에서 추세추종 edge가 비용에 갈리는 게 명확해서 1h로 옮긴 결과를 본다.
파라미터 시험은 여전히 in-sample 만 (명세 §6.2).
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.run import (  # noqa: E402
    DATA_DIR,
    IN_SAMPLE_RATIO,
    load_klines,
    prepare_indicators,
    print_report,
    run_backtest,
    summarize,
)
from strategy.rules import StrategyParams  # noqa: E402


SCENARIOS = [
    ("v0.1 baseline (crossover + partial_tp)", StrategyParams()),
    ("v0.2b trailing only (crossover + trailing ATR×2.0)",
     StrategyParams(exit_mode="trailing", trail_atr_mult=2.0)),
    ("v0.2b' trailing ATR×3.0",
     StrategyParams(exit_mode="trailing", trail_atr_mult=3.0)),
    ("v0.1 + tighter CCI (cci=±130)",
     StrategyParams(cci_entry_long=130.0, cci_entry_short=-130.0)),
    ("trailing + tighter CCI (cci=±130, trail ×2.0)",
     StrategyParams(cci_entry_long=130.0, cci_entry_short=-130.0,
                    exit_mode="trailing", trail_atr_mult=2.0)),
]


def main() -> None:
    import argparse
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser()
    ap.add_argument("--main-tf", default="1h")
    ap.add_argument("--htf", default="4h")
    args = ap.parse_args()

    df_main = load_klines(DATA_DIR / f"BTCUSDT-{args.main_tf}.csv")
    df_htf = load_klines(DATA_DIR / f"BTCUSDT-{args.htf}.csv")
    base_prep = prepare_indicators(df_main, df_htf, StrategyParams())
    split = int(len(base_prep) * IN_SAMPLE_RATIO)
    is_df = base_prep.iloc[:split].reset_index(drop=True)
    print(f"메인 {args.main_tf} / HTF {args.htf}  | IS 봉 수: {len(is_df):,}  "
          f"({is_df['close_time'].iloc[0].date()} ~ {is_df['close_time'].iloc[-1].date()})")

    print("\n" + "=" * 80)
    print(f"{'시나리오':<55} {'트레이드':>8} {'수익률':>10} {'MDD':>8} {'PF':>6}")
    print("=" * 80)
    results = []
    for label, params in SCENARIOS:
        state = run_backtest(is_df, params)
        rep = summarize(state, label, is_df["close_time"].iloc[0], is_df["close_time"].iloc[-1])
        pf = rep["profit_factor"]
        pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(
            f"{label[:55]:<55} {rep['trades']:>8} "
            f"{rep['total_return_pct']:>+9.2f}% {rep['max_drawdown_pct']:>7.2f}% {pf_str:>6}"
        )
        results.append((label, rep))
    print("=" * 80)
    for label, rep in results:
        rep["label"] = label
        print_report(rep)


if __name__ == "__main__":
    main()
