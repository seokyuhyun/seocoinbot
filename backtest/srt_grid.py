"""SRT 1차 그리드 서치 — IS only.

YT 비교에서 SRT 가 유일하게 PF > 1 (1.05) 였음. 자막 정의를 그대로 옮긴
디폴트 값에서 다음 3축만 흔들어 양수 조합이 있는지 확인:

  - trix_required: TRIX 부호 필터 의무 (True) vs 무시 (False)
  - rr           : 손절폭 대비 익절 배수 (1.5 / 2.0 / 3.0)
  - swing_lookback: 손절가 산정용 최근 봉 수 (5 / 10 / 20)

= 2 × 3 × 3 = 18 조합. 과적합 위험 의식해서 일부러 작게 잡음.

IS 70% 만 사용. OOS 는 명세 §6.2 보호 — 이미 1회 사용했고 결과 -9.99% 였음.
여기서 IS 양수 후보가 나오면 OOS 검증은 별도 1회 단발 결정.
"""

from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.run import (  # noqa: E402
    DATA_DIR,
    IN_SAMPLE_RATIO,
    load_klines,
    summarize,
)
from backtest.yt_engine import run_yt_backtest  # noqa: E402
from strategy.yt_strategies import SRTParams, SRTStrategy  # noqa: E402


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="SRT 1차 그리드 서치 (IS only)")
    ap.add_argument("--main-tf", default="1h")
    return ap.parse_args()


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    df = load_klines(DATA_DIR / f"BTCUSDT-{args.main_tf}.csv")
    split = int(len(df) * IN_SAMPLE_RATIO)
    is_df = df.iloc[:split].reset_index(drop=True)
    print(f"IS: {is_df['close_time'].iloc[0]} ~ {is_df['close_time'].iloc[-1]}  "
          f"({len(is_df):,}봉)\n")

    trix_grid = [True, False]
    rr_grid = [1.5, 2.0, 3.0]
    swing_grid = [5, 10, 20]

    rows = []
    for trix_req, rr, swing in itertools.product(trix_grid, rr_grid, swing_grid):
        params = SRTParams(
            trix_required=trix_req,
            rr=rr,
            swing_lookback=swing,
        )
        strat = SRTStrategy(params)
        prepared = strat.prepare(is_df)
        state = run_yt_backtest(prepared, strat)
        rep = summarize(
            state,
            f"trix={trix_req!s:<5} rr={rr:.1f} swing={swing:>2}",
            is_df["close_time"].iloc[0],
            is_df["close_time"].iloc[-1],
        )
        rows.append(rep)

    # 표 출력 (수익률 내림차순)
    rows.sort(key=lambda r: r["total_return_pct"], reverse=True)
    print(f"{'설정':<32} {'거래':>5} {'수익률':>9} {'MDD':>7} {'PF':>6} {'승률':>6}")
    print("-" * 75)
    for r in rows:
        pf = r["profit_factor"]
        pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(
            f"{r['label']:<32} "
            f"{r['trades']:>5} "
            f"{r['total_return_pct']:>+8.2f}% "
            f"{r['max_drawdown_pct']:>6.2f}% "
            f"{pf_s:>6} "
            f"{r['win_rate_pct']:>5.1f}%"
        )

    positives = [r for r in rows if r["total_return_pct"] > 0]
    print(f"\n양수 수익률 조합: {len(positives)}/{len(rows)}")
    if positives:
        best = positives[0]
        print(f"최고: {best['label']}  → +{best['total_return_pct']:.2f}% "
              f"PF {best['profit_factor']:.2f}")


if __name__ == "__main__":
    main()
