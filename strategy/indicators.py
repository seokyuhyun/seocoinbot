"""표준 정의대로 계산한 기술적 지표 — pandas만 사용.

설계서 2절·백테스트 명세서 3절에서 요구한 ADX/CCI/RSI/EMA를 제공한다.
모두 마감된 캔들만 입력으로 받으며, 미래 데이터를 참조하지 않는다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder smoothing = EMA with alpha = 1/period
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def cci(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20) -> pd.Series:
    tp = (high + low + close) / 3.0
    sma = tp.rolling(period).mean()
    mad = (tp - sma).abs().rolling(period).mean()
    return (tp - sma) / (0.015 * mad)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """평균진폭. Wilder 평활. 트레일링 스탑 거리 산출에 사용."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0.0), up_move, 0.0),
        index=high.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0.0), down_move, 0.0),
        index=high.index,
    )

    alpha = 1.0 / period
    atr = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr
    minus_di = 100.0 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=alpha, adjust=False).mean()


# ─────────────────────────────────────────────────────────────
# 추가 지표 — 유튜브 4전략용 (WM/더블바텀/더블탑/SRT)
# ─────────────────────────────────────────────────────────────


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """표준 MACD. line / signal / histogram 3개 컬럼 반환."""
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


def stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_period: int = 14,
    k_smooth: int = 3,
    d_period: int = 3,
) -> pd.DataFrame:
    """슬로우 스토캐스틱. (14, 3, 3) 이 영상 자막의 기본값."""
    hh = high.rolling(k_period).max()
    ll = low.rolling(k_period).min()
    raw_k = 100.0 * (close - ll) / (hh - ll).replace(0, np.nan)
    k = raw_k.rolling(k_smooth).mean()
    d = k.rolling(d_period).mean()
    return pd.DataFrame({"stoch_k": k, "stoch_d": d})


def trix(close: pd.Series, period: int = 10) -> pd.Series:
    """삼중 EMA 의 1봉 변화율(%). 자막의 TRIX 0선 기준 진입필터에 사용."""
    e1 = ema(close, period)
    e2 = ema(e1, period)
    e3 = ema(e2, period)
    return 100.0 * (e3 / e3.shift(1) - 1.0)


def bollinger(close: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    """볼린저밴드. middle/upper/lower 3개 컬럼."""
    mid = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    return pd.DataFrame({
        "bb_middle": mid,
        "bb_upper": mid + num_std * std,
        "bb_lower": mid - num_std * std,
    })


def rsi_signal(close: pd.Series, period: int = 14, signal_period: int = 14) -> pd.DataFrame:
    """RSI 본선 + 시그널선 (RSI 의 EMA).

    자막의 "RSI 보라색이 노란색을 상향 돌파" 는 RSI 본선이 RSI 의 평활선을
    돌파한다는 뜻으로 해석 (TradingView 의 RSI+MA 오버레이 관행).
    """
    r = rsi(close, period)
    return pd.DataFrame({"rsi": r, "rsi_sig": ema(r, signal_period)})


# ─────────────────────────────────────────────────────────────
# 패턴 보조 — 로컬 극값
# ─────────────────────────────────────────────────────────────


def rolling_argmin(series: pd.Series, window: int) -> pd.Series:
    """과거 window 봉 안에서 최솟값의 인덱스. 더블바텀 패턴 인식용."""
    return series.rolling(window).apply(lambda x: x.argmin(), raw=True)


def rolling_argmax(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).apply(lambda x: x.argmax(), raw=True)
