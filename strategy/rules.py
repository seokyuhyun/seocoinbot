"""전략 규칙 — 순수 함수.

설계서 2.4~2.7절을 코드로 옮긴 v0.1 규칙 + 백테스트 결과를 반영해
파라미터 옵션으로 추가한 진입·청산 변형들. 이 함수들은 백테스트에서
호출되는지 실거래에서 호출되는지 모른다 (설계서 7.5절).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


Side = Literal["LONG", "SHORT"]
EntryMode = Literal["crossover", "extension"]
ExitMode = Literal["partial_tp", "trailing"]


@dataclass(frozen=True)
class IndicatorSnapshot:
    """마감된 15분봉 시점에 가용한 지표 값들."""

    adx: float
    cci_now: float
    cci_prev: float
    rsi: float
    close_15m: float
    ema50_htf: float
    atr: Optional[float] = None  # 트레일링용. 없으면 트레일링 모드 사용 불가.


@dataclass(frozen=True)
class StrategyParams:
    """v0.1 출발점 값. 백테스트 결과에 따라 조정 후 v1.0 확정 (설계서 서두).

    기본값은 명세서 v0.1 그대로. 그리드 서치에서 더 나은 조합이 확인되면
    여기 default 를 갱신한다.
    """

    # 진입 공통
    adx_threshold: float = 25.0
    cci_entry_long: float = 100.0
    cci_entry_short: float = -100.0
    rsi_long_min: float = 50.0
    rsi_long_max: float = 70.0
    rsi_short_min: float = 30.0
    rsi_short_max: float = 50.0

    # 손절·익절
    stop_loss_pct: float = 0.01      # 진입가 대비 ±1.0% (설계서 2.7)
    tp1_r_multiple: float = 1.0      # 1R 부분익절 (설계서 2.7)
    tp2_r_multiple: float = 2.0      # 2R 부분익절

    # 모드 스위치 (백테스트로 진입·청산 구조 차이 비교용)
    entry_mode: EntryMode = "crossover"
    exit_mode: ExitMode = "partial_tp"

    # exit_mode == "trailing" 일 때만 사용
    trail_atr_mult: float = 2.0      # 트레일링 폭 = trail_atr_mult × ATR
    # 1R 도달 시 손절을 진입가(BE)로 옮긴 뒤 트레일링 시작. TP2·CCI0 익절 없음.


# ─────────────────────────────────────────────────────────────
# HTF 필터 & 청산 보조
# ─────────────────────────────────────────────────────────────


def htf_filter(snap: IndicatorSnapshot) -> Side:
    """1시간봉 EMA50 필터. close > EMA50이면 롱만, 아래면 숏만 허용."""
    return "LONG" if snap.close_15m > snap.ema50_htf else "SHORT"


def cci_zero_cross_exit(side: Side, cci_now: float, cci_prev: float) -> bool:
    """CCI 0선 반대 방향 돌파 = 추세 힘 빠짐 = 보유 포지션 청산 트리거."""
    if side == "LONG":
        return cci_prev >= 0.0 > cci_now
    return cci_prev <= 0.0 < cci_now


# ─────────────────────────────────────────────────────────────
# 진입 — crossover (v0.1 기본)
# ─────────────────────────────────────────────────────────────


def _crossover_long(snap: IndicatorSnapshot, p: StrategyParams) -> bool:
    return (
        snap.cci_prev <= p.cci_entry_long
        and snap.cci_now > p.cci_entry_long
    )


def _crossover_short(snap: IndicatorSnapshot, p: StrategyParams) -> bool:
    return (
        snap.cci_prev >= p.cci_entry_short
        and snap.cci_now < p.cci_entry_short
    )


# ─────────────────────────────────────────────────────────────
# 진입 — extension (그리드 결과 후 검증용)
#
# v0.1 "막 +100 돌파한 캔들" 진입은 스파이크 꼭대기를 잡는 경향이 보였다.
# extension 은 임계 위에서 *지속 + 상승* 중일 때만 들어간다 — 즉 1캔들이라도
# 추세가 살아 있다는 사후 확인 후 진입.
# ─────────────────────────────────────────────────────────────


def _extension_long(snap: IndicatorSnapshot, p: StrategyParams) -> bool:
    return (
        snap.cci_now > p.cci_entry_long
        and snap.cci_now > snap.cci_prev   # 여전히 위로 가속 중
    )


def _extension_short(snap: IndicatorSnapshot, p: StrategyParams) -> bool:
    return (
        snap.cci_now < p.cci_entry_short
        and snap.cci_now < snap.cci_prev
    )


# ─────────────────────────────────────────────────────────────
# 진입 — public API (모드 스위치)
# ─────────────────────────────────────────────────────────────


def long_entry_signal(snap: IndicatorSnapshot, p: StrategyParams) -> bool:
    """롱 진입 6개 조건 중 5번(포지션 없음)·6번(쿨다운)은 호출자가 검사."""
    base_ok = (
        htf_filter(snap) == "LONG"
        and snap.adx >= p.adx_threshold
        and p.rsi_long_min < snap.rsi < p.rsi_long_max
    )
    if not base_ok:
        return False
    if p.entry_mode == "extension":
        return _extension_long(snap, p)
    return _crossover_long(snap, p)


def short_entry_signal(snap: IndicatorSnapshot, p: StrategyParams) -> bool:
    """숏 진입 — 롱의 거울 대칭."""
    base_ok = (
        htf_filter(snap) == "SHORT"
        and snap.adx >= p.adx_threshold
        and p.rsi_short_min < snap.rsi < p.rsi_short_max
    )
    if not base_ok:
        return False
    if p.entry_mode == "extension":
        return _extension_short(snap, p)
    return _crossover_short(snap, p)
