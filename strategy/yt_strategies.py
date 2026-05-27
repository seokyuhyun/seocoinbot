"""유튜브 자료 4전략 — IS 비교용 신호·청산 정의.

사용자가 정리해 준 자막 5전략 중 4번(하이타이트 플래그)은 알트코인 전용이라
BTC 데이터로 검증 불가, 제외. 나머지 4개:

  1) WM   — MACD 히스토그램 약화 + 종가 10MA 돌파  (추세+모멘텀)
  2) DBL_BOTTOM — TRIX < 0 부근 더블바텀 패턴 (롱 전용, 평균회귀)
  3) DBL_TOP    — RSI 베어리쉬 다이버전스 더블탑 (숏 전용, 평균회귀)
  4) SRT  — Stoch %K↗%D + RSI 본선↗시그널 (+TRIX 위치 보조)

각 전략은 (1) 진입 신호 (2) 손절가 (3) 익절가 를 자체적으로 산출한다.
backtest/yt_engine.py 가 이 인터페이스(StrategyHook)를 호출한다.

설계서 §6.1(다음 캔들 시가 체결)·명세서 §4(인트라바 SL 우선) 규약은 그대로
지킨다. 이 모듈은 신호만 만든다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol

import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────
# 공통 인터페이스
# ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Signal:
    """진입 신호. side + 손절가 + 익절가."""

    side: str          # "LONG" | "SHORT"
    stop_price: float  # 절대가격 (진입가가 아니라 손절 자체의 가격)
    tp_price: float    # 절대가격. 1차 익절(50%) 가격
    tp2_price: Optional[float] = None  # 2차 익절(50%). None 이면 단일 익절


class StrategyHook(Protocol):
    """전략별 신호 생성기."""

    name: str

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """입력 OHLCV 에 전략에 필요한 지표·보조 컬럼을 추가해 반환."""
        ...

    def signal(self, df: pd.DataFrame, i: int) -> Optional[Signal]:
        """i 봉 close 시점에서 평가. 진입 신호 없으면 None.

        주의: 마지막 봉은 i-1, i 가 모두 사용 가능. 미래 봉(i+1 이후)을
        절대 참조하지 말 것 (look-ahead 방지, 명세서 §6.1).
        """
        ...


# ─────────────────────────────────────────────────────────────
# 1) WM — MACD + MA10/30
# ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WMParams:
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    ma_short: int = 10
    ma_long: int = 30
    swing_lookback: int = 10   # 손절용 최근 저점/고점 산정 봉 수
    tp2_lookback: int = 20     # 2차 익절 = 최근 N봉 고점/저점


class WMStrategy:
    """자막 정리:
      롱:  MACD 하락 히스토그램 절댓값 감소 + 종가 10MA 위
      숏:  MACD 상승 히스토그램 절댓값 감소 + 종가 10MA 아래
      손절: 최근 N봉 저점(롱)/고점(숏)
      익절: 1차 30MA, 2차 최근 N봉 고점/저점
    """

    name = "WM"

    def __init__(self, p: WMParams = WMParams()) -> None:
        self.p = p

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        from strategy.indicators import macd as _macd, sma  # 늦은 import (순환 회피)

        out = df.copy()
        m = _macd(out["close"], self.p.macd_fast, self.p.macd_slow, self.p.macd_signal)
        out["macd_hist"] = m["macd_hist"]
        out["macd_hist_prev"] = out["macd_hist"].shift(1)
        out["ma_short"] = sma(out["close"], self.p.ma_short)
        out["ma_long"] = sma(out["close"], self.p.ma_long)
        out["recent_low"] = out["low"].rolling(self.p.swing_lookback).min()
        out["recent_high"] = out["high"].rolling(self.p.swing_lookback).max()
        out["far_low"] = out["low"].rolling(self.p.tp2_lookback).min()
        out["far_high"] = out["high"].rolling(self.p.tp2_lookback).max()
        return out

    def signal(self, df: pd.DataFrame, i: int) -> Optional[Signal]:
        if i < max(self.p.macd_slow, self.p.tp2_lookback, self.p.ma_long) + 5:
            return None
        row = df.iloc[i]
        if pd.isna(row["macd_hist"]) or pd.isna(row["macd_hist_prev"]):
            return None
        h_now, h_prev = row["macd_hist"], row["macd_hist_prev"]
        close = row["close"]
        ma_s, ma_l = row["ma_short"], row["ma_long"]
        if pd.isna(ma_s) or pd.isna(ma_l):
            return None

        # 롱: 히스토그램 음수에서 약화 중(절댓값 감소) + 종가 > MA10
        if h_now < 0 and h_prev < 0 and h_now > h_prev and close > ma_s:
            stop = float(row["recent_low"])
            if stop >= close:    # 손절이 현재가 이상이면 무효
                return None
            # 1차 익절 = MA30 (위쪽이어야 의미 있음 — 아니면 단순 R 기반 fallback)
            tp = float(ma_l) if ma_l > close else close + (close - stop) * 1.0
            tp2 = float(row["far_high"])
            if tp2 <= tp:
                tp2 = None
            return Signal("LONG", stop, tp, tp2)

        # 숏: 히스토그램 양수에서 약화 중 + 종가 < MA10
        if h_now > 0 and h_prev > 0 and h_now < h_prev and close < ma_s:
            stop = float(row["recent_high"])
            if stop <= close:
                return None
            tp = float(ma_l) if ma_l < close else close - (stop - close) * 1.0
            tp2 = float(row["far_low"])
            if tp2 >= tp:
                tp2 = None
            return Signal("SHORT", stop, tp, tp2)

        return None


# ─────────────────────────────────────────────────────────────
# 2) 더블 바텀 (TRIX 0선 아래)  — 롱 전용
# ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DoubleBottomParams:
    lookback: int = 30          # 두 저점을 찾는 범위
    pivot_window: int = 3       # 좌우 N봉보다 낮으면 피벗 저점
    tol_pct: float = 0.005      # 두 저점 가격 차이 허용 (±0.5%)
    trix_period: int = 10
    sl_buffer_pct: float = 0.005  # 두 저점 이하 0.5% 아래에 손절


class DoubleBottomStrategy:
    """자막:
      두 저점이 비슷 + TRIX < 0 + 가격 반등 확인 → 롱
      손절: 두 저점 하단 (이전 하락폭의 절반 = 손절폭 자체가 작아짐)
      익절: 두 저점 사이 고점
    """

    name = "DBL_BOTTOM"

    def __init__(self, p: DoubleBottomParams = DoubleBottomParams()) -> None:
        self.p = p

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        from strategy.indicators import trix as _trix

        out = df.copy()
        out["trix"] = _trix(out["close"], self.p.trix_period)
        # 피벗 저점: 좌우 pivot_window 봉보다 낮은 low (centered)
        w = self.p.pivot_window
        rolled_min = out["low"].rolling(2 * w + 1, center=True).min()
        out["is_pivot_low"] = (out["low"] == rolled_min) & out["low"].notna()
        return out

    def signal(self, df: pd.DataFrame, i: int) -> Optional[Signal]:
        # 중앙형 피벗은 우측 w봉을 봐야 확정 → look-ahead 방지: i-w 까지의 피벗만 사용
        w = self.p.pivot_window
        if i < self.p.lookback + w + 5:
            return None
        # i 봉의 신호 평가에서 사용할 수 있는 가장 최근 확정 피벗은 i-w
        confirm_idx = i - w
        if confirm_idx < self.p.lookback:
            return None

        row = df.iloc[i]
        if pd.isna(row["trix"]) or row["trix"] >= 0:
            return None

        # confirm_idx 이전 lookback 봉 안에서 확정된 pivot_low 후보들
        start = max(0, confirm_idx - self.p.lookback)
        window = df.iloc[start:confirm_idx + 1]
        pivots = window[window["is_pivot_low"] == True]
        if len(pivots) < 2:
            return None
        # 가장 최근 두 저점
        l2 = pivots.iloc[-1]
        l1 = pivots.iloc[-2]
        # 두 저점이 ±tol_pct 안에서 비슷해야 더블바텀
        if l1["low"] <= 0:
            return None
        if abs(l2["low"] - l1["low"]) / l1["low"] > self.p.tol_pct:
            return None
        # 두 저점 사이 봉들의 고점 = 1차 익절
        between = df.iloc[int(window.index[window["is_pivot_low"]][-2]):int(window.index[window["is_pivot_low"]][-1]) + 1]
        if between.empty or "high" not in between.columns:
            return None
        mid_high = float(between["high"].max())
        # 현재 봉 close 가 두 저점보다 위로 회복 + mid_high 아래일 때 진입
        close = float(row["close"])
        bottom = float(min(l1["low"], l2["low"]))
        if close <= bottom or close >= mid_high:
            return None
        stop = bottom * (1.0 - self.p.sl_buffer_pct)
        tp = mid_high
        return Signal("LONG", stop, tp)


# ─────────────────────────────────────────────────────────────
# 3) 더블 탑 (RSI 베어리쉬 다이버전스) — 숏 전용
# ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DoubleTopParams:
    lookback: int = 30
    pivot_window: int = 3
    price_min_break_pct: float = 0.0  # 두번째 고점 >= 첫번째 고점 (0 이면 같거나 위)
    rsi_period: int = 14
    sl_extra_pct: float = 0.3   # 직전 상승폭의 30% 위 (자막)


class DoubleTopStrategy:
    name = "DBL_TOP"

    def __init__(self, p: DoubleTopParams = DoubleTopParams()) -> None:
        self.p = p

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        from strategy.indicators import rsi as _rsi

        out = df.copy()
        out["rsi"] = _rsi(out["close"], self.p.rsi_period)
        w = self.p.pivot_window
        rolled_max = out["high"].rolling(2 * w + 1, center=True).max()
        out["is_pivot_high"] = (out["high"] == rolled_max) & out["high"].notna()
        return out

    def signal(self, df: pd.DataFrame, i: int) -> Optional[Signal]:
        w = self.p.pivot_window
        if i < self.p.lookback + w + 5:
            return None
        confirm_idx = i - w
        if confirm_idx < self.p.lookback:
            return None

        row = df.iloc[i]
        start = max(0, confirm_idx - self.p.lookback)
        window = df.iloc[start:confirm_idx + 1]
        pivots = window[window["is_pivot_high"] == True]
        if len(pivots) < 2:
            return None
        h2 = pivots.iloc[-1]
        h1 = pivots.iloc[-2]

        # 두 번째 고점이 첫 번째 고점 이상
        if h2["high"] < h1["high"] * (1.0 + self.p.price_min_break_pct):
            return None
        # RSI 베어리쉬 다이버전스: 가격은 갱신, RSI 는 낮음
        if pd.isna(h1["rsi"]) or pd.isna(h2["rsi"]):
            return None
        if h2["rsi"] >= h1["rsi"]:
            return None

        # 두 고점 사이 저점 = 익절 후보
        between = df.iloc[int(window.index[window["is_pivot_high"]][-2]):int(window.index[window["is_pivot_high"]][-1]) + 1]
        if between.empty:
            return None
        mid_low = float(between["low"].min())
        close = float(row["close"])
        # 현재가가 두 번째 고점 아래로 떨어졌고 mid_low 까지는 안 갔을 때 진입
        if close >= h2["high"] or close <= mid_low:
            return None

        # 직전 상승폭 = mid_low → h2["high"]
        rise = float(h2["high"]) - mid_low
        if rise <= 0:
            return None
        stop = float(h2["high"]) + rise * self.p.sl_extra_pct
        if stop <= close:
            return None
        # 익절: 이전 상승폭만큼 하락 = mid_low
        tp = mid_low
        if tp >= close:
            return None
        return Signal("SHORT", stop, tp)


# ─────────────────────────────────────────────────────────────
# 4) SRT — Stoch + RSI + TRIX
# ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SRTParams:
    stoch_k: int = 14
    stoch_smooth: int = 3
    stoch_d: int = 3
    rsi_period: int = 14
    rsi_signal_period: int = 14
    trix_period: int = 10
    swing_lookback: int = 10
    rr: float = 2.0          # 익절 = 손절폭의 RR 배수
    # 튜닝 옵션
    trix_required: bool = True   # False 면 TRIX 부호 무시
    # 과매수/과매도 zone 제약 — 롱은 K 가 이 값 이하에서 cross 일 때만, 숏은 그 반대.
    # None 이면 무제한.
    stoch_long_max_k: Optional[float] = None
    stoch_short_min_k: Optional[float] = None
    use_trail_be: bool = False   # 1R 도달 시 손절을 진입가(BE)로 이동


class SRTStrategy:
    """자막:
      롱:  Stoch %K 상향돌파 %D + RSI 본선 상향돌파 시그널 + TRIX<0(좋은 자리)
      숏:  대칭
      손절: 최근 저점/고점
      익절: 자막에는 "미리 정한다"만 — 손절폭의 N배 R 로 정의 (기본 2R)
      TRIX 는 "더 좋은 자리" 보조라 필수로 둘지 옵션화. 여기선 필수로 둠.
    """

    name = "SRT"

    def __init__(self, p: SRTParams = SRTParams()) -> None:
        self.p = p

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        from strategy.indicators import stochastic, rsi_signal, trix as _trix

        out = df.copy()
        s = stochastic(out["high"], out["low"], out["close"],
                       self.p.stoch_k, self.p.stoch_smooth, self.p.stoch_d)
        out["stoch_k"] = s["stoch_k"]
        out["stoch_d"] = s["stoch_d"]
        out["stoch_k_prev"] = out["stoch_k"].shift(1)
        out["stoch_d_prev"] = out["stoch_d"].shift(1)
        r = rsi_signal(out["close"], self.p.rsi_period, self.p.rsi_signal_period)
        out["srsi"] = r["rsi"]
        out["srsi_sig"] = r["rsi_sig"]
        out["srsi_prev"] = out["srsi"].shift(1)
        out["srsi_sig_prev"] = out["srsi_sig"].shift(1)
        out["s_trix"] = _trix(out["close"], self.p.trix_period)
        out["s_recent_low"] = out["low"].rolling(self.p.swing_lookback).min()
        out["s_recent_high"] = out["high"].rolling(self.p.swing_lookback).max()
        return out

    def signal(self, df: pd.DataFrame, i: int) -> Optional[Signal]:
        if i < max(self.p.stoch_k, self.p.rsi_period, self.p.trix_period,
                   self.p.swing_lookback) + 5:
            return None
        row = df.iloc[i]
        needed = ("stoch_k", "stoch_d", "stoch_k_prev", "stoch_d_prev",
                  "srsi", "srsi_sig", "srsi_prev", "srsi_sig_prev", "s_trix")
        if any(pd.isna(row[c]) for c in needed):
            return None

        k, d = row["stoch_k"], row["stoch_d"]
        kp, dp = row["stoch_k_prev"], row["stoch_d_prev"]
        r_, rs = row["srsi"], row["srsi_sig"]
        rp, rsp = row["srsi_prev"], row["srsi_sig_prev"]
        t = row["s_trix"]
        close = float(row["close"])

        stoch_up_cross = kp <= dp and k > d
        stoch_dn_cross = kp >= dp and k < d
        rsi_up_cross = rp <= rsp and r_ > rs
        rsi_dn_cross = rp >= rsp and r_ < rs

        trix_ok_long = (not self.p.trix_required) or t < 0
        trix_ok_short = (not self.p.trix_required) or t > 0
        zone_ok_long = (self.p.stoch_long_max_k is None) or k <= self.p.stoch_long_max_k
        zone_ok_short = (self.p.stoch_short_min_k is None) or k >= self.p.stoch_short_min_k

        if stoch_up_cross and rsi_up_cross and trix_ok_long and zone_ok_long:
            stop = float(row["s_recent_low"])
            if stop >= close:
                return None
            risk = close - stop
            if self.p.use_trail_be:
                # tp1=1R (50% 익절+BE 이동), tp2=RR*R (잔여 50% 익절)
                return Signal("LONG", stop, close + risk, close + risk * self.p.rr)
            return Signal("LONG", stop, close + risk * self.p.rr)
        if stoch_dn_cross and rsi_dn_cross and trix_ok_short and zone_ok_short:
            stop = float(row["s_recent_high"])
            if stop <= close:
                return None
            risk = stop - close
            if self.p.use_trail_be:
                return Signal("SHORT", stop, close - risk, close - risk * self.p.rr)
            return Signal("SHORT", stop, close - risk * self.p.rr)
        return None


# ─────────────────────────────────────────────────────────────
# 레지스트리
# ─────────────────────────────────────────────────────────────


ALL_STRATEGIES: list[StrategyHook] = [
    WMStrategy(),
    DoubleBottomStrategy(),
    DoubleTopStrategy(),
    SRTStrategy(),
]
