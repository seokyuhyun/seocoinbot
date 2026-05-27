"""파라미터 그리드 서치 — in-sample 70%에서만 탐색 (명세서 6.2).

전략 v0.1 의 출발점 값이 IS/OOS 모두 손실로 끝났다. 명세서 8절의 흐름대로:
  4. in-sample(앞 70%)로 실행, 결과 지표 확인
  5. 결과가 부실하면 → 전략 규칙/파라미터 수정 → 4번 반복

여기서는 ADX 임계, CCI 진입선, 손절 폭, TP 배수를 조합해 IS 에서만 탐색한다.
OOS 검증은 결과를 보고 사용자가 결정. (OOS 는 단 한 번만 써야 함)
"""

from __future__ import annotations

import itertools
import sys
import time
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtest.run import (  # noqa: E402
    DATA_DIR,
    IN_SAMPLE_RATIO,
    RESULTS_DIR,
    INITIAL_EQUITY,
    load_klines,
    prepare_15m_with_indicators,
    run_backtest,
    summarize,
)
from strategy.rules import StrategyParams  # noqa: E402


# ─────────────────────────────────────────────────────────────
# 그리드 정의 — 4 × 2 × 3 × 2 = 48 조합
# ─────────────────────────────────────────────────────────────
GRID = {
    "adx_threshold": [25.0, 28.0, 30.0, 33.0],
    "cci_entry_abs": [100.0, 130.0],       # 롱은 +값, 숏은 -값으로 미러링
    "stop_loss_pct": [0.008, 0.010, 0.012],
    "tp_pair": [(1.0, 2.0), (1.5, 3.0)],   # (TP1_R, TP2_R)
}


def make_params(adx_t: float, cci_abs: float, stop: float, tp: tuple) -> StrategyParams:
    return StrategyParams(
        adx_threshold=adx_t,
        cci_entry_long=cci_abs,
        cci_entry_short=-cci_abs,
        stop_loss_pct=stop,
        tp1_r_multiple=tp[0],
        tp2_r_multiple=tp[1],
    )


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")

    df15 = load_klines(DATA_DIR / "BTCUSDT-15m.csv")
    df1h = load_klines(DATA_DIR / "BTCUSDT-1h.csv")
    base_prep = prepare_15m_with_indicators(df15, df1h, StrategyParams())
    split = int(len(base_prep) * IN_SAMPLE_RATIO)
    is_df = base_prep.iloc[:split].reset_index(drop=True)
    print(f"IS 봉 수: {len(is_df):,}  ({is_df['close_time'].iloc[0]} ~ {is_df['close_time'].iloc[-1]})")

    combos = list(itertools.product(*GRID.values()))
    print(f"\n총 {len(combos)}개 조합 그리드 서치 시작...\n")

    rows = []
    t0 = time.perf_counter()
    for k, (adx_t, cci_abs, stop, tp) in enumerate(combos, start=1):
        params = make_params(adx_t, cci_abs, stop, tp)
        state = run_backtest(is_df, params)
        rep = summarize(state, "IS", is_df["close_time"].iloc[0], is_df["close_time"].iloc[-1])
        pf = rep["profit_factor"]
        rows.append({
            "adx": adx_t,
            "cci_abs": cci_abs,
            "stop_pct": stop,
            "tp1_r": tp[0],
            "tp2_r": tp[1],
            "trades": rep["trades"],
            "return_pct": rep["total_return_pct"],
            "mdd_pct": rep["max_drawdown_pct"],
            "win_rate_pct": rep["win_rate_pct"],
            "profit_factor": (None if pf == float("inf") else pf),
            "max_consec_losses": rep["max_consecutive_losses"],
            "fees": rep["total_fees"],
        })
        elapsed = time.perf_counter() - t0
        eta = elapsed / k * (len(combos) - k)
        print(
            f"[{k:>2}/{len(combos)}] adx={adx_t:>4.1f} cci=±{cci_abs:>5.1f} "
            f"stop={stop*100:.2f}% TP={tp[0]:.1f}R/{tp[1]:.1f}R  "
            f"→ ret {rep['total_return_pct']:>+7.2f}% / MDD {rep['max_drawdown_pct']:>5.2f}% / "
            f"trades {rep['trades']:>4}  "
            f"(경과 {elapsed:.0f}s, 남은 ~{eta:.0f}s)"
        )

    results = pd.DataFrame(rows).sort_values("return_pct", ascending=False).reset_index(drop=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / "grid_search_is.csv"
    results.to_csv(out_path, index=False)

    print("\n" + "=" * 78)
    print("Top 10 by total return (IS only)")
    print("=" * 78)
    print(results.head(10).to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    print(f"\n전체 결과 저장: {out_path}")

    best = results.iloc[0]
    print("\n" + "=" * 78)
    print(f"최고 조합: adx={best['adx']}, cci=±{best['cci_abs']}, "
          f"stop={best['stop_pct']*100:.2f}%, TP={best['tp1_r']}R/{best['tp2_r']}R")
    print(f"  → IS 수익률 {best['return_pct']:+.2f}% / MDD {best['mdd_pct']:.2f}% "
          f"/ trades {int(best['trades'])} / PF {best['profit_factor']}")
    print("=" * 78)
    print("\n주의 (명세 §6.2): OOS 는 단 한 번만 사용. 위 결과로 충분히 만족 후에만 검증.")


if __name__ == "__main__":
    main()
