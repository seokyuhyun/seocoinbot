"""OOS 단일 검증 — 명세서 §6.2 의 '딱 한 번' 규칙.

IS 에서 확정한 후보 조합 (1h main + 4h HTF + CCI ±130 + 트레일링 ATR×2.0)
을 out-of-sample 구간(뒤 30%)에서 단 한 번만 평가한다.

이 스크립트를 두 번 이상 돌리면 OOS 가 사실상 IS 가 된다 — 그 후의 결정은
모두 과최적화 위험을 안고 가는 것임을 인지하고 진행한다.

실행:
    python backtest/verify_oos.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.run import (  # noqa: E402
    DATA_DIR,
    IN_SAMPLE_RATIO,
    dump_artifacts,
    load_klines,
    prepare_indicators,
    print_report,
    run_backtest,
    summarize,
)
from strategy.rules import StrategyParams  # noqa: E402


# IS 에서 도출한 후보 조합 — 변경 금지 (변경하면 OOS 가 IS 화 됨)
CANDIDATE = StrategyParams(
    cci_entry_long=130.0,
    cci_entry_short=-130.0,
    exit_mode="trailing",
    trail_atr_mult=2.0,
)
MAIN_TF = "1h"
HTF_TF = "4h"


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    df_main = load_klines(DATA_DIR / f"BTCUSDT-{MAIN_TF}.csv")
    df_htf = load_klines(DATA_DIR / f"BTCUSDT-{HTF_TF}.csv")
    prep = prepare_indicators(df_main, df_htf, CANDIDATE)
    split = int(len(prep) * IN_SAMPLE_RATIO)
    oos_df = prep.iloc[split:].reset_index(drop=True)

    print(f"OOS 구간: {oos_df['close_time'].iloc[0]} ~ {oos_df['close_time'].iloc[-1]}")
    print(f"OOS 봉 수: {len(oos_df):,}")
    print(f"\n후보 파라미터: {CANDIDATE}\n")

    state = run_backtest(oos_df, CANDIDATE)
    rep = summarize(
        state,
        f"OOS VERIFICATION ({MAIN_TF}/{HTF_TF}, CCI±130, trailing ATR×2.0)",
        oos_df["close_time"].iloc[0],
        oos_df["close_time"].iloc[-1],
    )
    print_report(rep)
    dump_artifacts(state, f"{MAIN_TF}_oos_final")

    print("\n참고 — IS 결과 (이미 본 값):")
    print("  거래 67  /  수익률 +6.74%  /  MDD -8.92%  /  PF 1.29  /  승률 34.3%")
    print("\nIS 와 OOS 가 비슷하게 양수·낮은 MDD 면 → 전략 v1.0 후보 확정")
    print("OOS 가 크게 무너지면 → 과최적화. 전략 재검토 (명세 §6.2)")


if __name__ == "__main__":
    main()
