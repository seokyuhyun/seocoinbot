"""ORB-C v4 walk-forward — 16개월 4분기 분할 검증.

체크 포인트:
1. 전체 PF 가 fold 별로 어떻게 변하는가
2. v4.1 baseline 의 6개 winner (BTC/ETH/WLD/PEPE/NEAR/ONDO) 가 fold 마다 양수인가
3. 시간 따라 좋아지는 패턴이 진짜인가 우연인가
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402

from backtest.orbc_v4 import (  # noqa: E402
    SYMBOLS_POOL, load_data, backtest,
)


FOLDS = [
    ("Q1", "2025-01-01", "2025-04-30"),
    ("Q2", "2025-05-01", "2025-08-31"),
    ("Q3", "2025-09-01", "2025-12-31"),
    ("Q4", "2026-01-01", "2026-04-30"),
]

WINNERS = ["BTCUSDT", "ETHUSDT", "WLDUSDT", "1000PEPEUSDT", "NEARUSDT", "ONDOUSDT"]


def per_coin_pf(trades_df, sym):
    s = trades_df[trades_df["symbol"] == sym]
    if s.empty:
        return float("nan"), 0, 0.0
    gp = s.loc[s["pnl"] > 0, "pnl"].sum()
    gl = s.loc[s["pnl"] <= 0, "pnl"].sum()
    pf = gp / abs(gl) if gl < 0 else float("inf")
    return pf, len(s), s["pnl"].sum()


def main():
    t0 = time.time()
    print(f"심볼 {len(SYMBOLS_POOL)}개 로딩...")
    data = load_data(SYMBOLS_POOL)

    print(f"\n{'='*70}")
    print(f"ORB-C v4 Walk-forward K={len(FOLDS)}")
    print(f"{'='*70}\n")

    fold_rows = []
    per_coin_per_fold = {sym: {} for sym in SYMBOLS_POOL}

    for fold_name, s_date, e_date in FOLDS:
        print(f"--- {fold_name} ({s_date} ~ {e_date}) ---")
        summary, trades_df, curve_df = backtest(data, start_date=s_date, end_date=e_date)
        pf = summary["pf"]
        pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(f"  거래 {summary['trades']:>4}  "
              f"수익률 {summary['return']*100:>+6.2f}%  "
              f"MDD {summary['mdd']*100:>+6.2f}%  "
              f"PF {pf_s:>5}  "
              f"승률 {summary['win_rate']:>4.1f}%  "
              f"L/S {summary['long_trades']}/{summary['short_trades']}")
        fold_rows.append({
            "fold": fold_name, "period": f"{s_date}~{e_date}",
            "trades": summary["trades"],
            "return_pct": summary["return"] * 100,
            "mdd_pct": summary["mdd"] * 100,
            "pf": pf,
            "win_rate": summary["win_rate"],
        })
        for sym in SYMBOLS_POOL:
            pf_c, n_c, pnl_c = per_coin_pf(trades_df, sym)
            per_coin_per_fold[sym][fold_name] = {"pf": pf_c, "n": n_c, "pnl": pnl_c}

    print(f"\n{'='*70}")
    print("전체 fold 요약")
    print(f"{'='*70}\n")
    df = pd.DataFrame(fold_rows)
    print(df.to_string(index=False))

    print(f"\n{'='*70}")
    print("심볼 × Fold PnL 매트릭스 (USDT)")
    print(f"{'='*70}\n")
    matrix = []
    for sym in SYMBOLS_POOL:
        row = {"symbol": sym}
        total = 0.0
        pos_folds = 0
        for f, _, _ in FOLDS:
            pnl = per_coin_per_fold[sym][f[:2]]["pnl"] if f[:2] in per_coin_per_fold[sym] else 0
            n = per_coin_per_fold[sym][f[:2]]["n"] if f[:2] in per_coin_per_fold[sym] else 0
            row[f] = f"{pnl:>+7.0f}({n})"
            total += pnl
            if pnl > 0:
                pos_folds += 1
        row["total"] = f"{total:>+7.0f}"
        row["pos"] = f"{pos_folds}/4"
        matrix.append(row)
    print(pd.DataFrame(matrix).to_string(index=False))

    print(f"\n{'='*70}")
    print("WINNERS (v4.1 baseline 양수 6개) — fold 별 일관성")
    print(f"{'='*70}\n")
    winner_summary = []
    for sym in WINNERS:
        if sym not in per_coin_per_fold:
            continue
        row = {"symbol": sym}
        pos_folds = 0
        total_pnl = 0.0
        total_n = 0
        for f, _, _ in FOLDS:
            data = per_coin_per_fold[sym].get(f)
            if data:
                pf_v = data["pf"]
                pf_s = "inf" if pf_v == float("inf") else (f"{pf_v:.2f}" if not pd.isna(pf_v) else "-")
                row[f] = f"{data['pnl']:>+6.0f}/{data['n']}({pf_s})"
                total_pnl += data["pnl"]
                total_n += data["n"]
                if data["pnl"] > 0:
                    pos_folds += 1
            else:
                row[f] = "-"
        row["total"] = f"{total_pnl:>+6.0f}/{total_n}"
        row["pos_folds"] = f"{pos_folds}/4"
        winner_summary.append(row)
    print(pd.DataFrame(winner_summary).to_string(index=False))

    print(f"\n{'='*70}")
    print("판정")
    print(f"{'='*70}\n")
    avg_pf = sum(r["pf"] for r in fold_rows if r["pf"] != float("inf")) / len(fold_rows)
    pos_folds_total = sum(1 for r in fold_rows if r["return_pct"] > 0)
    print(f"전체 평균 PF: {avg_pf:.2f}")
    print(f"전체 양수 fold: {pos_folds_total}/{len(FOLDS)}")
    print()
    winner_pos = sum(
        1 for sym in WINNERS
        if sum(1 for f, _, _ in FOLDS if per_coin_per_fold[sym].get(f, {}).get("pnl", 0) > 0) >= 3
    )
    print(f"WINNERS 중 3+/4 fold 양수 코인 수: {winner_pos}/{len(WINNERS)}")
    print()
    if winner_pos >= 4:
        print("→ WINNERS 신뢰도 충분 → v4.2 (whitelist) 추진 가능")
    else:
        print("→ WINNERS 도 fold 별 일관성 부족 → 우연일 가능성 큼 → 추가 작업 보류")

    print(f"\n실행 시간: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
