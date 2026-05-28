"""Trend Breakout — 1H 추세 + 15m 거래량 돌파 전략 (사용자 v2).

타임프레임:
- 1H: 추세 필터 (BTC + 코인 자체). BTC 단일 거래 시 두 필터 동일.
- 15m: 진입 신호.

진입 조건 (LONG, strict 모드 = 모두 충족):
  1) BTC 1H close > BTC EMA200 × (1 + 0.003)   ← BTC 상승장 필터 (±0.3% 무거래 zone)
  2) 코인 1H close > EMA200                     ← 단일코인 모드선 (1) 과 동일
  3) 15m EMA20 > EMA50
  4) 15m close > 직전 20 15m 고점 (현재봉 제외)
  5) 15m volume >= 20봉 평균(SMA20, 현재봉 포함) × 1.5
  6) 15m RSI(14) ∈ [52, 72]
  7) 15m ATR%(14) >= 0.35%
  8) clamp 후 손절폭 ∈ [0.4%, 2.5%]              ← 8) 만 강제 (1~7 strict)

SHORT: 미러 (RSI 28~48, 저점 이탈 등).

손절:
- LONG SL 후보1 = Entry - ATR×1.3
       SL 후보2 = 최근 10 15m 저점 (현재봉 포함)
       최종 SL = MIN (둘 중 더 낮은 가격 = 더 보수적)
- SHORT 미러: MAX
- 손절폭 clamp:
    min 0.4% → 강제 적용 (계산값이 더 좁으면 0.4% 로 늘림)
    max 2.5% → 신호 무효 (너무 위험)

익절: 1R / 2R / 3R 분할
- TP1 = 1R, 원래 qty 의 40% 청산
- TP2 = 2R, 원래 qty 의 30% 청산
- TP3 = 3R, 원래 qty 의 30% 청산 (= 남은 전량)
- TP1 후 SL → Entry (본절 방어)
- SL/TP 같은 봉 충돌 시 SL 우선 (명세서 §4)

사이즈 (리스크 기반):
- 1회 최대 손실 = base_equity × 0.005 (0.5%)
- notional = 허용 손실금 / 손절폭 비율
  예) 10,000 × 0.005 = 50 USDT 손실 / 0.02 = 2,500 USDT notional
- 레버리지: 사용자 지정 (1/2/3 sweep). margin = notional / leverage
- notional 캡: equity × leverage (마진 부족 방지)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import pandas as pd

from strategy.indicators import atr, ema, rsi, sma


@dataclass(frozen=True)
class TBParams:
    # 1H 추세
    ema_long: int = 200
    btc_buffer_pct: float = 0.003   # ±0.3%

    # 15m 진입
    ema_short: int = 20
    ema_mid: int = 50
    rsi_period: int = 14
    long_rsi_min: float = 52.0
    long_rsi_max: float = 72.0
    short_rsi_min: float = 28.0
    short_rsi_max: float = 48.0
    breakout_lookback: int = 20
    vol_sma_period: int = 20
    vol_mult: float = 1.5
    atr_period: int = 14
    min_atr_pct: float = 0.0035     # 0.35%

    # 손절
    atr_mult: float = 1.3
    swing_lookback: int = 10
    sl_min_pct: float = 0.004       # 0.4%
    sl_max_pct: float = 0.025       # 2.5%

    # 익절 (원래 qty 의 비율; 합 = 1.0)
    tp_r_multiples: Tuple[float, ...] = (1.0, 2.0, 3.0)
    tp_fractions: Tuple[float, ...] = (0.40, 0.30, 0.30)

    # 리스크 / 레버리지
    risk_per_trade_pct: float = 0.005
    leverage: float = 1.0
    maintenance_mmr: float = 0.005


def prepare_15m(df_15m: pd.DataFrame, p: TBParams) -> pd.DataFrame:
    """15m OHLCV → 지표 부착."""
    out = df_15m.copy()
    out["ema_short"] = ema(out["close"], p.ema_short)
    out["ema_mid"] = ema(out["close"], p.ema_mid)
    out["rsi"] = rsi(out["close"], p.rsi_period)
    out["atr"] = atr(out["high"], out["low"], out["close"], p.atr_period)
    out["atr_pct"] = out["atr"] / out["close"]
    out["vol_sma"] = sma(out["volume"], p.vol_sma_period)
    # 돌파는 직전 N봉 (현재 제외): shift(1) 후 rolling
    out["recent_high"] = out["high"].shift(1).rolling(p.breakout_lookback).max()
    out["recent_low"] = out["low"].shift(1).rolling(p.breakout_lookback).min()
    # 스윙 손절은 현재 포함 N봉 (보수): rolling
    out["swing_low"] = out["low"].rolling(p.swing_lookback).min()
    out["swing_high"] = out["high"].rolling(p.swing_lookback).max()
    return out


def prepare_1h(df_1h: pd.DataFrame, p: TBParams) -> pd.DataFrame:
    """1h → EMA200 만 필요. close_time 정렬."""
    out = df_1h.copy()
    out["ema_long_1h"] = ema(out["close"], p.ema_long)
    out = out.rename(columns={"close": "close_1h"})
    return out[["close_time", "close_1h", "ema_long_1h"]].sort_values("close_time").reset_index(drop=True)


def merge_tf(df_15m_prep: pd.DataFrame, df_1h_prep: pd.DataFrame) -> pd.DataFrame:
    """15m 각 봉에 '가장 최근 닫힌' 1h 값을 backward-fill 로 정렬.

    look-ahead 차단: 15m close_time t 에서 사용할 1h 값은 close_time <= t 인
    가장 최근 1h 봉의 값. merge_asof(direction='backward') 가 정확히 그 의미.
    """
    a = df_15m_prep.sort_values("close_time").reset_index(drop=True)
    b = df_1h_prep.sort_values("close_time").reset_index(drop=True)
    merged = pd.merge_asof(a, b, on="close_time", direction="backward")
    return merged


# ─────────────────────────────────────────────────────────────
# 신호 생성 — 봉 i CLOSE 시점에서 평가, 진입은 봉 i+1 OPEN 에서
# ─────────────────────────────────────────────────────────────


def long_signal(row: pd.Series, p: TBParams) -> Optional[dict]:
    """반환: 진입 후보 dict 또는 None.
    dict 키: side, stop_ref (signal 시점 기준 손절가), sl_pct_ref, ref_price.
    실제 fill 가격에서 다시 sl_pct 산출하므로 ref 는 가이드용.
    """
    needed = ("close_1h", "ema_long_1h", "ema_short", "ema_mid",
              "rsi", "atr", "atr_pct", "vol_sma",
              "recent_high", "swing_low", "close", "volume")
    if any(pd.isna(row[c]) for c in needed):
        return None
    # 1) BTC 1H 상승장 (close_1h > EMA200 × 1.003)
    if row["close_1h"] <= row["ema_long_1h"] * (1.0 + p.btc_buffer_pct):
        return None
    # 2) 코인 1H close > EMA200 (BTC 단일이라 위와 사실상 중복; 보존)
    if row["close_1h"] <= row["ema_long_1h"]:
        return None
    # 3) 15m EMA20 > EMA50
    if row["ema_short"] <= row["ema_mid"]:
        return None
    # 4) 직전 20봉 고점 돌파
    if row["close"] <= row["recent_high"]:
        return None
    # 5) 거래량 1.5배 이상
    if row["volume"] < row["vol_sma"] * p.vol_mult:
        return None
    # 6) RSI band
    if not (p.long_rsi_min <= row["rsi"] <= p.long_rsi_max):
        return None
    # 7) ATR%
    if row["atr_pct"] < p.min_atr_pct:
        return None
    # 8) 손절 후보 계산 — Entry 기준은 일단 signal close 사용
    entry_ref = float(row["close"])
    sl_atr = entry_ref - float(row["atr"]) * p.atr_mult
    sl_swing = float(row["swing_low"])
    sl = min(sl_atr, sl_swing)           # 더 낮은 (=더 보수)
    sl_pct = (entry_ref - sl) / entry_ref
    # clamp
    if sl_pct > p.sl_max_pct:
        return None                      # 너무 위험 → 신호 무효
    if sl_pct < p.sl_min_pct:
        sl = entry_ref * (1.0 - p.sl_min_pct)
        sl_pct = p.sl_min_pct
    return {"side": "LONG", "stop_ref": sl, "sl_pct_ref": sl_pct, "ref_price": entry_ref}


def short_signal(row: pd.Series, p: TBParams) -> Optional[dict]:
    needed = ("close_1h", "ema_long_1h", "ema_short", "ema_mid",
              "rsi", "atr", "atr_pct", "vol_sma",
              "recent_low", "swing_high", "close", "volume")
    if any(pd.isna(row[c]) for c in needed):
        return None
    # 1) BTC 1H 하락장 (close_1h < EMA200 × 0.997)
    if row["close_1h"] >= row["ema_long_1h"] * (1.0 - p.btc_buffer_pct):
        return None
    # 2) 코인 1H close < EMA200
    if row["close_1h"] >= row["ema_long_1h"]:
        return None
    # 3) 15m EMA20 < EMA50
    if row["ema_short"] >= row["ema_mid"]:
        return None
    # 4) 직전 20봉 저점 이탈
    if row["close"] >= row["recent_low"]:
        return None
    # 5) 거래량 패닉
    if row["volume"] < row["vol_sma"] * p.vol_mult:
        return None
    # 6) RSI
    if not (p.short_rsi_min <= row["rsi"] <= p.short_rsi_max):
        return None
    # 7) ATR%
    if row["atr_pct"] < p.min_atr_pct:
        return None
    # 8) 손절
    entry_ref = float(row["close"])
    sl_atr = entry_ref + float(row["atr"]) * p.atr_mult
    sl_swing = float(row["swing_high"])
    sl = max(sl_atr, sl_swing)
    sl_pct = (sl - entry_ref) / entry_ref
    if sl_pct > p.sl_max_pct:
        return None
    if sl_pct < p.sl_min_pct:
        sl = entry_ref * (1.0 + p.sl_min_pct)
        sl_pct = p.sl_min_pct
    return {"side": "SHORT", "stop_ref": sl, "sl_pct_ref": sl_pct, "ref_price": entry_ref}
