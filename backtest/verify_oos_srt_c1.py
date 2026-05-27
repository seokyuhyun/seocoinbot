"""SRT C1 후보 OOS 단발 검증 — 이 데이터셋의 마지막 OOS 호출.

후보 C1: SRTParams(trix_required=False, rr=1.5, swing_lookback=20)
  - IS 전체: +109.23% / MDD 26.82% / PF 1.44 / 거래 236
  - Walk-forward (K=4): 양수율 75%, 평균 PF 1.52, 최저 PF 0.96

명세 §6.2 의 정신상 이 OOS 구간(2025-09-24 ~ 2026-04-30)은
이미 v0.1 검증에 사용됐다 (결과 -9.99%). 새 메커니즘(SRT)으로 한 번 더
사용하는 것은 borderline 이지만:
  - 새 후보 선택은 v0.1 OOS fills 를 보지 않고 진행
  - IS+그리드+walk-forward 의 3단 통과
  - 결과에 무관하게 같은 OOS 를 더 만지지 않기로 사전 약속

→ 한 번만 돌리고 끝낸다.

실행:
    python backtest/verify_oos_srt_c1.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.run import (  # noqa: E402
    DATA_DIR,
    RESULTS_DIR,
    IN_SAMPLE_RATIO,
    load_klines,
    summarize,
    print_report,
)
from backtest.yt_engine import run_yt_backtest  # noqa: E402
from strategy.yt_strategies import SRTParams, SRTStrategy  # noqa: E402


C1 = SRTParams(trix_required=False, rr=1.5, swing_lookback=20)


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")

    df = load_klines(DATA_DIR / "BTCUSDT-1h.csv")
    split = int(len(df) * IN_SAMPLE_RATIO)
    oos_df = df.iloc[split:].reset_index(drop=True)
    print(f"OOS 구간: {oos_df['close_time'].iloc[0]} ~ {oos_df['close_time'].iloc[-1]}")
    print(f"OOS 봉 수: {len(oos_df):,}\n")
    print(f"후보 C1: {C1}\n")

    strat = SRTStrategy(C1)
    prepared = strat.prepare(oos_df)
    state = run_yt_backtest(prepared, strat)
    rep = summarize(
        state,
        "SRT C1 OOS",
        oos_df["close_time"].iloc[0],
        oos_df["close_time"].iloc[-1],
    )
    print_report(rep)

    # 아티팩트
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    import pandas as pd
    eq = pd.DataFrame(state.equity_curve, columns=["time", "equity"])
    eq.to_csv(RESULTS_DIR / "equity_srt_c1_oos.csv", index=False)
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
    pd.DataFrame(fr).to_csv(RESULTS_DIR / "fills_srt_c1_oos.csv", index=False)

    print("\n참고 — IS 전체 결과 (이미 본 값):")
    print("  거래 236  /  +109.23%  /  MDD -26.82%  /  PF 1.44  /  승률 48.7%")
    print("Walk-forward (K=4): 양수율 75%, 평균 PF 1.52, 최저 PF 0.96")
    print("\n분기:")
    print("  OOS 가 양수·PF ≥ 1.1 → 전략 v1.0 후보 확정 (paper trade 단계로)")
    print("  OOS 가 음수 → 이 데이터셋 종료. 다른 메커니즘/TF/자산 재고")


if __name__ == "__main__":
    main()
