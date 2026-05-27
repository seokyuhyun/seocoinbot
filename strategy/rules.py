"""전략 규칙 v0.1 — 순수 함수.

설계서 2.4~2.7절을 그대로 코드로 옮긴 것. 입력은 마감된 캔들의 지표값과
포지션 상태, 출력은 진입·청산 신호다. 이 함수는 자신이 백테스트에서
호출되는지 실거래에서 호출되는지 모른다 (설계서 7.5절).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Side = Literal["LONG", "SHORT"]


@dataclass(frozen=True)
class IndicatorSnapshot:
    """마감된 15분봉 시점에 가용한 지표 값들."""

    adx: float
    cci_now: float
    cci_prev: float
    rsi: float
    close_15m: float
    ema50_1h: float


@dataclass(frozen=True)
class StrategyParams:
    """v0.1 출발점 값. 백테스트 결과에 따라 조정 후 v1.0 확정 (설계서 서두)."""

    adx_threshold: float = 25.0
    cci_entry_long: float = 100.0
    cci_entry_short: float = -100.0
    rsi_long_min: float = 50.0
    rsi_long_max: float = 70.0
    rsi_short_min: float = 30.0
    rsi_short_max: float = 50.0
    stop_loss_pct: float = 0.01  # 진입가 대비 ±1.0% (설계서 2.7)
    tp1_r_multiple: float = 1.0  # 1R 부분익절 (설계서 2.7)
    tp2_r_multiple: float = 2.0  # 2R 부분익절


def htf_filter(snap: IndicatorSnapshot) -> Side:
    """1시간봉 EMA50 필터. close > EMA50이면 롱만, 아래면 숏만 허용."""
    return "LONG" if snap.close_15m > snap.ema50_1h else "SHORT"


def long_entry_signal(snap: IndicatorSnapshot, p: StrategyParams) -> bool:
    """롱 진입 6개 조건 중 5번(포지션 없음)·6번(쿨다운)은 호출자가 검사."""
    return (
        htf_filter(snap) == "LONG"
        and snap.adx >= p.adx_threshold
        and snap.cci_prev <= p.cci_entry_long
        and snap.cci_now > p.cci_entry_long
        and p.rsi_long_min < snap.rsi < p.rsi_long_max
    )


def short_entry_signal(snap: IndicatorSnapshot, p: StrategyParams) -> bool:
    """숏 진입 — 롱의 거울 대칭."""
    return (
        htf_filter(snap) == "SHORT"
        and snap.adx >= p.adx_threshold
        and snap.cci_prev >= p.cci_entry_short
        and snap.cci_now < p.cci_entry_short
        and p.rsi_short_min < snap.rsi < p.rsi_short_max
    )


def cci_zero_cross_exit(side: Side, cci_now: float, cci_prev: float) -> bool:
    """CCI 0선 반대 방향 돌파 = 추세 힘 빠짐 = 보유 포지션 청산 트리거."""
    if side == "LONG":
        return cci_prev >= 0.0 > cci_now
    return cci_prev <= 0.0 < cci_now
