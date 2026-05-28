"""v0.1 (crossover + partial_tp) 대 v0.2 (extension + trailing) IS 비교.

이 비교는 in-sample 70% 에서만 수행한다. v0.2 가 IS 에서 명확하게 우월하지
않으면 OOS 로 가지 않는다 (명세서 6.2: OOS 는 단 한 번만 검증용으로).

또한 ADX·CCI 임계도 그리드 서치에서 더 나은 영역(adx=33, cci=±130)을
함께 비교해 진입 모드·청산 모드·임계 효과를 분리한다.
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
    prepare_15m_with_indicators,
    print_report,
    run_backtest,
    summarize,
)
from strategy.rules import StrategyParams  # noqa: E402


SCENARIOS = [
    # (라벨, params)
    ("v0.1 baseline (crossover, partial_tp, default thresholds)",
     StrategyParams()),
    ("v0.1 best-of-grid (crossover, partial_tp, adx=33, cci=±130)",
     StrategyParams(adx_threshold=33.0, cci_entry_long=130.0, cci_entry_short=-130.0,
                    stop_loss_pct=0.008)),
    ("v0.2a extension only (default thresholds, partial_tp)",
     StrategyParams(entry_mode="extension")),
    ("v0.2b trailing only (crossover, ATR×2.0 trail)",
     StrategyParams(exit_mode="trailing", trail_atr_mult=2.0)),
    ("v0.2c extension + trailing (default thresholds)",
     StrategyParams(entry_mode="extension", exit_mode="trailing", trail_atr_mult=2.0)),
    ("v0.2d extension + trailing + grid-best thresholds",
     StrategyParams(adx_threshold=33.0, cci_entry_long=130.0, cci_entry_short=-130.0,
                    stop_loss_pct=0.008,
                    entry_mode="extension", exit_mode="trailing", trail_atr_mult=2.0)),
    ("v0.2e extension + trailing + ATR×3.0 (looser trail)",
     StrategyParams(entry_mode="extension", exit_mode="trailing", trail_atr_mult=3.0)),
]


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    df15 = load_klines(DATA_DIR / "BTCUSDT-15m.csv")
    df1h = load_klines(DATA_DIR / "BTCUSDT-1h.csv")
    base_prep = prepare_15m_with_indicators(df15, df1h, StrategyParams())
    split = int(len(base_prep) * IN_SAMPLE_RATIO)
    is_df = base_prep.iloc[:split].reset_index(drop=True)
    print(f"IS 봉 수: {len(is_df):,}  ({is_df['close_time'].iloc[0]} ~ {is_df['close_time'].iloc[-1]})")

    print("\n" + "=" * 78)
    print(f"{'시나리오':<60} {'트레이드':>8} {'수익률':>10} {'MDD':>8} {'PF':>6}")
    print("=" * 78)

    results = []
    for label, params in SCENARIOS:
        state = run_backtest(is_df, params)
        rep = summarize(state, label, is_df["close_time"].iloc[0], is_df["close_time"].iloc[-1])
        pf = rep["profit_factor"]
        pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(
            f"{label[:60]:<60} {rep['trades']:>8} "
            f"{rep['total_return_pct']:>+9.2f}% {rep['max_drawdown_pct']:>7.2f}% {pf_str:>6}"
        )
        results.append((label, rep))

    print("=" * 78)
    print("\n각 시나리오 상세:")
    for label, rep in results:
        rep["label"] = label
        print_report(rep)


if __name__ == "__main__":
    main()
