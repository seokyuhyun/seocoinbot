"""Pullback v3 walk-forward — 16개월을 4분기로 잘라 각 fold 백테스트.

목적: 전구간 결과 (-2.3%, 0.88 PF, short PF >1) 가 우연이 아닌지,
시간대별로 일관되게 양수·음수가 나오는지 확인.

판정:
- short PF 4 fold 중 3 이상 > 1.0 → SHORT edge 신뢰
- long PF 4 fold 중 0~1 만 > 1.0 → LONG 무 edge 결론
- 전체 PF 4 fold 평균 1.0 근접 → 양수 가능성

walk-forward 통과 = 다른 데이터셋에서도 비슷한 결과 기대 가능.
"""

from __future__ import annotations

import sys
import time
from datetime import timedelta

from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402

from backtest.pullback_v3 import (  # noqa: E402
    SYMBOLS, load_all_data, backtest,
)


FOLDS = [
    ("Q1", "2025-01-01", "2025-04-30"),
    ("Q2", "2025-05-01", "2025-08-31"),
    ("Q3", "2025-09-01", "2025-12-31"),
    ("Q4", "2026-01-01", "2026-04-30"),
]


def short_only_summary(trades_df):
    """SHORT 거래만으로 PF/승률 산출."""
    s = trades_df[trades_df["side"] == "short"]
    if s.empty:
        return {"trades": 0, "pnl": 0.0, "pf": float("nan"), "win_rate": 0.0}
    gp = s.loc[s["pnl"] > 0, "pnl"].sum()
    gl = s.loc[s["pnl"] <= 0, "pnl"].sum()
    pf = gp / abs(gl) if gl < 0 else float("inf")
    win_rate = (s["pnl"] > 0).mean() * 100
    return {"trades": len(s), "pnl": s["pnl"].sum(), "pf": pf, "win_rate": win_rate}


def long_only_summary(trades_df):
    s = trades_df[trades_df["side"] == "long"]
    if s.empty:
        return {"trades": 0, "pnl": 0.0, "pf": float("nan"), "win_rate": 0.0}
    gp = s.loc[s["pnl"] > 0, "pnl"].sum()
    gl = s.loc[s["pnl"] <= 0, "pnl"].sum()
    pf = gp / abs(gl) if gl < 0 else float("inf")
    win_rate = (s["pnl"] > 0).mean() * 100
    return {"trades": len(s), "pnl": s["pnl"].sum(), "pf": pf, "win_rate": win_rate}


def main():
    t0 = time.time()
    print("데이터 로딩 (캐시 사용)...")
    data = load_all_data(SYMBOLS)

    print(f"\n{'='*70}")
    print(f"Walk-forward K={len(FOLDS)} — 4분기 분할")
    print(f"{'='*70}\n")

    rows = []
    short_rows = []
    long_rows = []

    for fold_name, s_date, e_date in FOLDS:
        print(f"--- {fold_name} ({s_date} ~ {e_date}) ---")
        summary, trades_df, curve_df = backtest(data, start_date=s_date, end_date=e_date)

        pf = summary["pf"]
        pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"

        print(f"  거래 {summary['trades']:>3}  "
              f"수익률 {summary['return']*100:>+6.2f}%  "
              f"MDD {summary['mdd']*100:>+6.2f}%  "
              f"PF {pf_s:>5}  "
              f"승률 {summary['win_rate']:>4.1f}%  "
              f"L/S {summary['long_trades']}/{summary['short_trades']}")

        rows.append({
            "fold": fold_name, "period": f"{s_date}~{e_date}",
            "trades": summary["trades"],
            "return_pct": summary["return"] * 100,
            "mdd_pct": summary["mdd"] * 100,
            "pf": pf,
            "win_rate": summary["win_rate"],
            "longs": summary["long_trades"],
            "shorts": summary["short_trades"],
        })
        short_rows.append({"fold": fold_name, **short_only_summary(trades_df)})
        long_rows.append({"fold": fold_name, **long_only_summary(trades_df)})

    print(f"\n{'='*70}")
    print("전체 fold 요약")
    print(f"{'='*70}\n")
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))

    print(f"\n{'='*70}")
    print("SHORT 전용 fold 별")
    print(f"{'='*70}\n")
    sdf = pd.DataFrame(short_rows)
    print(sdf.to_string(index=False))

    print(f"\n{'='*70}")
    print("LONG 전용 fold 별")
    print(f"{'='*70}\n")
    ldf = pd.DataFrame(long_rows)
    print(ldf.to_string(index=False))

    print(f"\n{'='*70}")
    print("판정 요약")
    print(f"{'='*70}\n")

    pos_folds = sum(1 for r in rows if r["return_pct"] > 0)
    pf_above_1 = sum(1 for r in rows if r["pf"] > 1.0)
    avg_pf = sum(r["pf"] for r in rows if r["pf"] != float("inf")) / len(rows)

    short_pf_above_1 = sum(1 for r in short_rows if r["pf"] > 1.0)
    long_pf_above_1 = sum(1 for r in long_rows if r["pf"] > 1.0)

    print(f"전체 양수 fold:    {pos_folds}/{len(rows)}")
    print(f"전체 PF > 1.0:      {pf_above_1}/{len(rows)}")
    print(f"평균 PF:           {avg_pf:.2f}")
    print(f"SHORT PF > 1.0:    {short_pf_above_1}/{len(rows)} fold")
    print(f"LONG  PF > 1.0:    {long_pf_above_1}/{len(rows)} fold")

    print(f"\n실행 시간: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
