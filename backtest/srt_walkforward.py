"""SRT walk-forward 일관성 검증 — IS 구간을 K등분 후 각 fold 별 백테스트.

목적: 그리드에서 뽑은 후보가 단순히 IS 전체 평균에서만 좋은 게 아니라
       시간대별로도 일관되게 양수인지 확인. fold 의 양수율·PF 분포가
       나쁘면 우연이거나 과최적화. 좋으면 OOS 단발 검증으로 진행.

설정:
  - K = 4 (각 fold ≈ 4.2개월)
  - 후보 3개:
      C1: trix=False, rr=1.5, swing=20  (그리드 1위)
      C2: trix=False, rr=2.0, swing=20  (그리드 2위, 거래수 적당)
      C3: trix=False, rr=2.0, swing= 5  (그리드 3위, 짧은 손절)

  - 각 fold 시작 직전에 prepare 를 fold 자체 데이터로 한정해 다시 호출
    (지표 warmup 손실 발생 가능 — 보수적). 향후 walk-forward 의 정석은
    IS 시작점부터 fold 끝까지 데이터로 prepare 한 뒤 fold 구간만 평가
    하지만 여기서는 fold 독립성 확인이 목적이라 일단 단순 분할.

실행:
    python backtest/srt_walkforward.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402

from backtest.run import (  # noqa: E402
    DATA_DIR,
    IN_SAMPLE_RATIO,
    load_klines,
    summarize,
)
from backtest.yt_engine import run_yt_backtest  # noqa: E402
from strategy.yt_strategies import SRTParams, SRTStrategy  # noqa: E402


CANDIDATES = [
    ("C1", SRTParams(trix_required=False, rr=1.5, swing_lookback=20)),
    ("C2", SRTParams(trix_required=False, rr=2.0, swing_lookback=20)),
    ("C3", SRTParams(trix_required=False, rr=2.0, swing_lookback=5)),
]
K = 4


def run_fold(df: pd.DataFrame, params: SRTParams) -> dict:
    strat = SRTStrategy(params)
    prepared = strat.prepare(df)
    state = run_yt_backtest(prepared, strat)
    return summarize(
        state,
        "",
        df["close_time"].iloc[0],
        df["close_time"].iloc[-1],
    )


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    df = load_klines(DATA_DIR / "BTCUSDT-1h.csv")
    split = int(len(df) * IN_SAMPLE_RATIO)
    is_df = df.iloc[:split].reset_index(drop=True)
    n = len(is_df)
    fold_size = n // K
    folds = []
    for i in range(K):
        s = i * fold_size
        e = (i + 1) * fold_size if i < K - 1 else n
        folds.append(is_df.iloc[s:e].reset_index(drop=True))

    print(f"IS 봉 {n:,} → fold {K}개 (각 ≈ {fold_size:,}봉, ≈ {fold_size/24:.0f}일)")
    for i, f in enumerate(folds):
        print(f"  fold{i+1}: {f['close_time'].iloc[0].date()} ~ {f['close_time'].iloc[-1].date()}  ({len(f):,}봉)")
    print()

    # 각 후보 × fold
    results: dict[str, list[dict]] = {name: [] for name, _ in CANDIDATES}
    for name, params in CANDIDATES:
        for i, fdf in enumerate(folds):
            rep = run_fold(fdf, params)
            results[name].append(rep)

    # 표 출력
    print(f"{'후보':<5} {'설정':<28} | "
          + " | ".join(f"fold{i+1:>1}" for i in range(K))
          + " | 양수율 평균PF 최저PF")
    print("-" * (5 + 28 + 4 + (8 * K) + 30))
    for name, params in CANDIDATES:
        cfg = f"trix={params.trix_required!s:<5} rr={params.rr} swing={params.swing_lookback}"
        reps = results[name]
        cells = []
        rets = []
        pfs = []
        for r in reps:
            ret = r["total_return_pct"]
            pf = r["profit_factor"]
            pf_disp = "inf" if pf == float("inf") else pf
            rets.append(ret)
            if pf != float("inf"):
                pfs.append(pf)
            cells.append(f"{ret:>+6.1f}%")
        pos_rate = sum(1 for r in rets if r > 0) / len(rets) * 100
        avg_pf = sum(pfs) / len(pfs) if pfs else float("nan")
        min_pf = min(pfs) if pfs else float("nan")
        print(f"{name:<5} {cfg:<28} | "
              + " | ".join(cells)
              + f" | {pos_rate:>4.0f}%  {avg_pf:>5.2f}  {min_pf:>5.2f}")

    print()
    # 각 후보의 PF 별 세부
    for name, params in CANDIDATES:
        print(f"\n=== {name}  trix={params.trix_required} rr={params.rr} swing={params.swing_lookback} ===")
        for i, r in enumerate(results[name]):
            pf = r["profit_factor"]
            pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
            print(
                f"  fold{i+1}: "
                f"거래 {r['trades']:>3}  "
                f"{r['total_return_pct']:>+6.2f}%  "
                f"MDD {r['max_drawdown_pct']:>5.2f}%  "
                f"PF {pf_s:>4}  "
                f"승률 {r['win_rate_pct']:>4.1f}%"
            )


if __name__ == "__main__":
    main()
