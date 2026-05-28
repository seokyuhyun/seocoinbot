"""ORB-C v4 — Opening Range Breakout + Pullback Continuation (사용자 v2 직관 구현).

콘셉트:
  - KST 09:00 (UTC 00:00) = 세션 시작 (바이낸스 일일 통계·펀딩 변경 시각)
  - 첫 4시간 (UTC 00:00 ~ 04:00) = Opening Range 형성
  - OR 끝난 직후 20심볼 중 "OR 강도" 상위 2개 선정
    (거래량 + 방향성 + 변동성)
  - 선정된 코인이 OR high 돌파 후 풀백 → 양봉 반전 = LONG
    (LOW 이탈 + 음봉 반전 = SHORT 대칭)
  - SL = OR 반대편 ± ATR*0.5, TP = 1R/2R/3R 분할 (40/30/30)
  - 세션 끝(UTC 00:00) 강제 청산

ORB-C 는 stocks/futures 데이트레이딩의 검증된 패턴. 크립토 적용 시
session 정의가 모호하지만 UTC 00:00 = 일봉 시작·펀딩비 리셋 시각이라
가장 의미 있는 앵커.

리스크 관리:
  - 1% 리스크/거래
  - 최대 2 동시 포지션
  - 일일 -3% halt
  - 3 연속 손절 halt
  - Sunday session 스킵 (저유동성)
"""

from __future__ import annotations

import os
import sys
import subprocess
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone

try:
    import requests
    import numpy as np
    import pandas as pd
except ModuleNotFoundError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "numpy", "pandas"])
    import requests
    import numpy as np
    import pandas as pd

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "data" / "breakout_v2_cache"   # 기존 1h 캐시 재사용

# =========================
# 설정
# =========================

START = "2025-01-01"
END = "2026-04-30"

# 거래 대상 — 대형 메이저 우선, 저품질·갓상장 제외
SYMBOLS_POOL = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "LINKUSDT", "SUIUSDT", "NEARUSDT",
    "TAOUSDT", "WLDUSDT", "XLMUSDT", "ONDOUSDT", "1000PEPEUSDT",
]

INTERVAL = "1h"
START_EQUITY = 10_000.0

# 비용
FEE_RATE = 0.0006
SLIPPAGE = 0.0002
FUNDING_RATE = 0.0001
FUNDING_HOURS_UTC = (0, 8, 16)

# 세션 정의 (UTC 시각)
SESSION_START_HOUR = 0       # UTC 00:00 = KST 09:00
OR_HOURS = 4                 # 첫 4시간 = Opening Range
ENTRY_WINDOW_END_HOUR = 16   # UTC 16:00 까지만 신규 진입
SKIP_WEEKDAYS = (6,)         # Sunday 스킵 (weekday: Mon=0..Sun=6)

# 코인 선정 (v4.1: 완화)
TOP_N_COINS = 3              # 2 → 3
OR_VOL_MULT = 1.1            # 1.3 → 1.1
MIN_OR_RANGE_ATR = 0.5       # 0.8 → 0.5
MAX_OR_RANGE_PCT = 0.08      # 6% → 8%
MIN_OR_DIRECTION = 0.25      # 0.4 → 0.25

# 진입 (v4.1: 완화)
BREAKOUT_BUFFER_PCT = 0.0005    # 0.15% → 0.05%
PULLBACK_RETRACE_MIN = 0.15      # 0.30 → 0.15
PULLBACK_RETRACE_MAX = 0.85      # 0.65 → 0.85
CONFIRM_BODY_RATIO = 0.30        # 0.50 → 0.30

# 손절·익절 (v4.1: SL 을 풀백 low 기반으로 변경 = 더 타이트)
ATR_PERIOD = 14
SL_ATR_BUFFER = 0.3              # SL = 풀백 low ± ATR * 0.3
SL_MIN_PCT = 0.004
SL_MAX_PCT = 0.04
TP_R_MULTIPLES = (1.0, 2.0, 3.0)
TP_FRACTIONS = (0.40, 0.30, 0.30)
TRAIL_AFTER_TP2 = True

# 시간 제어 (v4.1: 세션 강제청산 제거, 24h 시간 손절)
TIME_STOP_BARS = 24              # 24h = 1 세션 (이전엔 SESSION_END 가 강제)
FORCE_CLOSE_AT_SESSION_END = False

# 리스크
RISK_PER_TRADE = 0.01            # 1%
LEVERAGE_CAP = 3.0
MAX_CONCURRENT = 3               # 2 → 3 (코인 선정 3 늘려서)
DAILY_LOSS_HALT = 0.03
CONSECUTIVE_STOPS = 3
COOLDOWN_AFTER_SL_BARS = 4       # 6 → 4

BINANCE_FAPI = "https://fapi.binance.com/fapi/v1"


# =========================
# 유틸
# =========================

def dt_utc(s):
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)

def ms(dt):
    return int(dt.timestamp() * 1000)

def interval_ms(interval):
    n = int(interval[:-1])
    u = interval[-1]
    return n * {"m": 60_000, "h": 3_600_000, "d": 86_400_000}[u]

def slip_open(px, side):
    return px * (1 + SLIPPAGE) if side == "long" else px * (1 - SLIPPAGE)

def slip_close(px, side):
    return px * (1 - SLIPPAGE) if side == "long" else px * (1 + SLIPPAGE)


# =========================
# 데이터
# =========================

def fetch_klines(symbol, interval, start, end, warmup_days=80):
    cache_path = CACHE_DIR / f"{symbol}_{interval}.csv"
    if cache_path.exists():
        df = pd.read_csv(cache_path)
        df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
        df = df.drop_duplicates("open_time").sort_values("open_time").set_index("open_time", drop=False)
        return df
    # (캐시 없으면 다운로드 — breakout_v2 와 같은 로직)
    start_dt = dt_utc(start) - timedelta(days=warmup_days)
    end_dt = dt_utc(end) + timedelta(days=1)
    cur = ms(start_dt); end_ms = ms(end_dt)
    rows = []; session = requests.Session(); backoff = 0.15
    while cur < end_ms:
        r = session.get(f"{BINANCE_FAPI}/klines", params={
            "symbol": symbol, "interval": interval,
            "startTime": cur, "endTime": end_ms - 1, "limit": 1500,
        }, timeout=20)
        if r.status_code == 429:
            wait = min(60.0, backoff * 4); time.sleep(wait); backoff = wait; continue
        if r.status_code != 200:
            raise RuntimeError(f"{symbol} {interval}: {r.status_code}")
        data = r.json()
        if not data: break
        rows.extend(data)
        nxt = int(data[-1][0]) + interval_ms(interval)
        if nxt <= cur: break
        cur = nxt; time.sleep(backoff)
    cols = ["open_time","open","high","low","close","volume","close_time",
            "qav","trades","tbbav","tbqav","ignore"]
    df = pd.DataFrame(rows, columns=cols)
    for c in ("open","high","low","close","volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.drop_duplicates("open_time").sort_values("open_time")
    df[["open_time","open","high","low","close","volume"]].to_csv(cache_path, index=False)
    df = df.set_index("open_time", drop=False)
    return df


# =========================
# 지표
# =========================

def atr_wilder(df, length=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/length, adjust=False, min_periods=length).mean()


def prepare(df):
    x = df.copy()
    x["atr"] = atr_wilder(x, ATR_PERIOD)
    x["vol_sma30d"] = x["volume"].rolling(24 * 30, min_periods=24*7).mean()  # 30일 평균 거래량
    return x


def load_data(symbols):
    data = {}
    for sym in symbols:
        try:
            df = fetch_klines(sym, INTERVAL, START, END)
            df = prepare(df)
            data[sym] = df
            print(f"  {sym} 1h 봉: {len(df):,}")
        except Exception as e:
            print(f"  {sym} 제외: {e}")
    return data


# =========================
# Session / Opening Range
# =========================

def compute_or_strength(or_bars, full_df_atr_30d_vol_at_close):
    """OR strength score = 거래량비 × 방향성 × 변동성. 높을수록 거래 후보 강함.
    or_bars: 4개 1h 캔들 DataFrame (UTC 00:00~04:00)
    """
    if len(or_bars) < OR_HOURS:
        return None
    open_px = float(or_bars["open"].iloc[0])
    close_px = float(or_bars["close"].iloc[-1])
    high_px = float(or_bars["high"].max())
    low_px = float(or_bars["low"].min())
    vol_sum = float(or_bars["volume"].sum())
    avg_vol = full_df_atr_30d_vol_at_close * OR_HOURS   # 4h 분량 추정
    atr = float(or_bars["atr"].iloc[-1])

    if not (np.isfinite(open_px) and np.isfinite(close_px) and np.isfinite(atr)) or atr <= 0:
        return None
    if high_px == low_px:
        return None

    or_range = high_px - low_px
    range_pct = or_range / open_px
    if range_pct > MAX_OR_RANGE_PCT:
        return None
    if or_range < atr * MIN_OR_RANGE_ATR:
        return None

    vol_mult = vol_sum / avg_vol if avg_vol > 0 else 0
    if vol_mult < OR_VOL_MULT:
        return None

    direction = (close_px - open_px) / or_range
    if abs(direction) < MIN_OR_DIRECTION:
        return None

    side = "long" if direction > 0 else "short"
    # Score: volume × |direction| × (range/atr)
    score = vol_mult * abs(direction) * (or_range / atr)
    return {
        "side": side,
        "or_high": high_px,
        "or_low": low_px,
        "or_open": open_px,
        "or_close": close_px,
        "or_range": or_range,
        "atr": atr,
        "vol_mult": vol_mult,
        "direction": direction,
        "score": score,
    }


# =========================
# 포지션
# =========================

class Position:
    def __init__(self, symbol, side, entry_idx, entry_time, entry_px, sl, qty,
                 risk_usdt, or_high, or_low, atr):
        self.symbol = symbol
        self.side = side
        self.entry_idx = entry_idx
        self.entry_time = entry_time
        self.entry = float(entry_px)
        self.sl_initial = float(sl)
        self.sl = float(sl)
        self.qty = float(qty)
        self.init_qty = float(qty)
        self.risk_usdt = risk_usdt
        self.or_high = or_high
        self.or_low = or_low
        self.atr = atr

        r = abs(self.entry - self.sl_initial)
        if side == "long":
            self.tps = [self.entry + r * m for m in TP_R_MULTIPLES]
        else:
            self.tps = [self.entry - r * m for m in TP_R_MULTIPLES]
        self.tp_done = [False, False, False]
        self.tp1_bar = None
        self.tp2_bar = None
        self.peak_price = self.entry   # for trailing

        self.gross_pnl = 0.0
        self.fees = self.entry * self.qty * FEE_RATE
        self.funding_paid = 0.0
        self.mae = 0.0
        self.mfe = 0.0
        self.exit_reason = None
        self.exit_time = None
        self.exit_price = None
        self.duration_bars = None

    def open(self):
        return self.qty > 1e-12

    def be_active(self, idx):
        return self.tp_done[0] and self.tp1_bar is not None and idx > self.tp1_bar

    def trailing_active(self, idx):
        return TRAIL_AFTER_TP2 and self.tp_done[1] and self.tp2_bar is not None and idx > self.tp2_bar

    def update_high(self, h, l):
        if self.side == "long":
            self.peak_price = max(self.peak_price, h)
            self.mfe = max(self.mfe, h / self.entry - 1)
            self.mae = min(self.mae, l / self.entry - 1)
        else:
            self.peak_price = min(self.peak_price, l) if self.peak_price > self.entry else min(self.peak_price, l)
            # 숏의 peak_price = lowest (favorable)
            self.peak_price = min(self.peak_price, l)
            self.mfe = max(self.mfe, self.entry / l - 1)
            self.mae = min(self.mae, self.entry / h - 1)

    def current_sl(self, idx):
        """trail / BE 적용한 현재 SL 가격."""
        if self.trailing_active(idx):
            # 트레일링: peak 에서 ATR*1.0 만큼 떨어진 곳
            if self.side == "long":
                trail = self.peak_price - self.atr * 1.0
                return max(self.entry, trail)   # BE 또는 트레일 중 더 가까운 (LONG: 높은)
            else:
                trail = self.peak_price + self.atr * 1.0
                return min(self.entry, trail)
        if self.be_active(idx):
            return self.entry
        return self.sl_initial

    def close_qty(self, qty, raw_px, reason, ts):
        qty = min(qty, self.qty)
        if qty <= 0:
            return 0.0
        px = slip_close(raw_px, self.side)
        if self.side == "long":
            pnl = qty * (px - self.entry)
        else:
            pnl = qty * (self.entry - px)
        fee = qty * px * FEE_RATE
        self.qty -= qty
        self.gross_pnl += pnl
        self.fees += fee
        if self.qty <= 1e-12:
            self.qty = 0.0
            self.exit_reason = reason
            self.exit_time = ts
            self.exit_price = px
        return pnl - fee

    def net_pnl(self):
        return self.gross_pnl - self.fees - self.funding_paid


def make_trade(p):
    return {
        "symbol": p.symbol,
        "side": p.side,
        "entry_time": p.entry_time,
        "exit_time": p.exit_time,
        "exit_reason": p.exit_reason,
        "entry": p.entry,
        "exit_price": p.exit_price,
        "duration_bars": p.duration_bars,
        "pnl": p.net_pnl(),
        "pnl_R": p.net_pnl() / p.risk_usdt if p.risk_usdt > 0 else 0,
        "fees": p.fees,
        "funding": p.funding_paid,
        "mae_pct": p.mae * 100,
        "mfe_pct": p.mfe * 100,
    }


# =========================
# 진입 로직
# =========================

def build_entry(side, or_info, entry_px, equity, pullback_extremum=None):
    """OR 정보 + 진입가 → SL/qty 산출. 거부 시 None.
    pullback_extremum: LONG=풀백 저점, SHORT=풀백 고점. None 이면 OR 반대편 사용."""
    ent = slip_open(float(entry_px), side)
    atr = or_info["atr"]
    if side == "long":
        # 풀백 저점 + ATR 버퍼 (없으면 OR low fallback)
        anchor = pullback_extremum if pullback_extremum is not None else or_info["or_low"]
        sl = anchor - atr * SL_ATR_BUFFER
        if sl >= ent:
            return None
        sl_pct = (ent - sl) / ent
    else:
        anchor = pullback_extremum if pullback_extremum is not None else or_info["or_high"]
        sl = anchor + atr * SL_ATR_BUFFER
        if sl <= ent:
            return None
        sl_pct = (sl - ent) / ent
    if sl_pct > SL_MAX_PCT:
        return None
    if sl_pct < SL_MIN_PCT:
        if side == "long":
            sl = ent * (1 - SL_MIN_PCT)
        else:
            sl = ent * (1 + SL_MIN_PCT)
        sl_pct = SL_MIN_PCT
    risk_usdt = equity * RISK_PER_TRADE
    qty = risk_usdt / abs(ent - sl)
    notional = qty * ent
    max_notional = equity * LEVERAGE_CAP
    if notional > max_notional:
        qty = max_notional / ent
    if qty <= 0:
        return None
    return {"entry": ent, "sl": sl, "qty": qty, "sl_pct": sl_pct, "risk_usdt": risk_usdt}


def detect_entry(side, or_info, recent_bars, atr):
    """recent_bars: 최근 N 봉. breakout → pullback → confirmation 체크.
    반환: 진입 신호 dict 또는 None."""
    if len(recent_bars) < 2:
        return None

    or_high = or_info["or_high"]
    or_low = or_info["or_low"]
    or_range = or_info["or_range"]

    # 직전 봉 (i-1) 까지 breakout 발생 여부 확인 (high/low 가 OR 밖으로 갔는지)
    bars_after_or = recent_bars
    if side == "long":
        broke_above = (bars_after_or["high"] > or_high * (1 + BREAKOUT_BUFFER_PCT)).any()
        if not broke_above:
            return None
        # 돌파 후 max high
        max_high = bars_after_or["high"].max()
        if max_high <= or_high:
            return None
        breakout_range = max_high - or_high
        # 현재 봉 (마지막) low = 풀백 지점
        cur = recent_bars.iloc[-1]
        cur_low = cur["low"]
        pullback_amount = max_high - cur_low
        # 풀백이 OR_high 까지 또는 그 아래로 내려갔는지 확인
        # 풀백 비율: (max_high - cur_low) / breakout_range. 단, cur_low 는 max_high 이하.
        if pullback_amount <= 0:
            return None
        # 풀백이 너무 깊으면 (OR_low 이하) 트레이드 무효
        if cur_low < or_low:
            return None
        # 풀백 비율 = (max_high - cur_low) / breakout_range
        # 30% ~ 65% 사이의 풀백만 인정
        retrace = pullback_amount / breakout_range if breakout_range > 0 else 0
        if not (PULLBACK_RETRACE_MIN <= retrace <= PULLBACK_RETRACE_MAX):
            return None
        # confirmation: 현재 봉 양봉 + body 비율
        body = cur["close"] - cur["open"]
        rng = cur["high"] - cur["low"]
        if body <= 0 or rng <= 0:
            return None
        if body / rng < CONFIRM_BODY_RATIO:
            return None
        # 풀백 저점 = cur_low (confirmation candle 의 low)
        return {"signal_close": cur["close"], "pullback_extremum": cur_low}
    else:  # short
        broke_below = (bars_after_or["low"] < or_low * (1 - BREAKOUT_BUFFER_PCT)).any()
        if not broke_below:
            return None
        min_low = bars_after_or["low"].min()
        if min_low >= or_low:
            return None
        breakout_range = or_low - min_low
        cur = recent_bars.iloc[-1]
        cur_high = cur["high"]
        pullback_amount = cur_high - min_low
        if pullback_amount <= 0:
            return None
        if cur_high > or_high:
            return None
        retrace = pullback_amount / breakout_range if breakout_range > 0 else 0
        if not (PULLBACK_RETRACE_MIN <= retrace <= PULLBACK_RETRACE_MAX):
            return None
        body = cur["open"] - cur["close"]
        rng = cur["high"] - cur["low"]
        if body <= 0 or rng <= 0:
            return None
        if body / rng < CONFIRM_BODY_RATIO:
            return None
        return {"signal_close": cur["close"], "pullback_extremum": cur_high}


# =========================
# 백테스트
# =========================

def max_drawdown(curve):
    if len(curve) == 0:
        return 0.0
    s = pd.Series(curve)
    return float((s / s.cummax() - 1).min())


def backtest(data, start_date=None, end_date=None, verbose=False):
    btc = data["BTCUSDT"]
    start_ts = pd.Timestamp(dt_utc(start_date or START))
    end_ts = pd.Timestamp(dt_utc(end_date or END) + timedelta(days=1))
    master = btc.index[(btc.index >= start_ts) & (btc.index < end_ts)].tolist()

    equity = START_EQUITY
    curve = []
    positions = {}
    trades = []
    cooldown_until = {s: -1 for s in data.keys()}

    daily_anchor_equity = equity
    current_day = None
    halt_today = False
    consec_stops = 0
    sessions_total = 0
    sessions_with_trade = 0

    # Session-local state
    selected_today = []      # 이번 세션에 거래할 코인 후보 [{symbol, or_info}]
    or_done = False

    for idx, ts in enumerate(master):
        if idx == 0:
            continue

        day_utc = ts.normalize()
        hour_utc = ts.hour
        weekday = day_utc.weekday()

        # 새 세션 시작 (UTC 00:00)
        if hour_utc == SESSION_START_HOUR and day_utc != current_day:
            if FORCE_CLOSE_AT_SESSION_END:
                for sym in list(positions.keys()):
                    p = positions[sym]
                    df = data[sym]
                    if ts in df.index:
                        raw_px = float(df.loc[ts, "open"])
                        equity += p.close_qty(p.qty, raw_px, "SESSION_END", ts)
                        p.duration_bars = idx - p.entry_idx
                        trades.append(make_trade(p))
                        del positions[sym]

            current_day = day_utc
            daily_anchor_equity = equity
            halt_today = False
            consec_stops = 0
            selected_today = []
            or_done = False
            sessions_total += 1

            # 일요일 스킵
            if weekday in SKIP_WEEKDAYS:
                halt_today = True

        # OR 형성 완료 시점 (UTC 04:00) — 코인 선정
        if not or_done and hour_utc == OR_HOURS and current_day is not None and not halt_today:
            # 각 심볼의 OR 데이터 수집
            or_start_ts = pd.Timestamp(day_utc).tz_localize("UTC") if day_utc.tz is None else day_utc
            or_end_ts = or_start_ts + pd.Timedelta(hours=OR_HOURS - 1)

            candidates = []
            for sym, df in data.items():
                # OR 4개 봉
                or_bars = df[(df.index >= or_start_ts) & (df.index <= or_end_ts)]
                if len(or_bars) < OR_HOURS:
                    continue
                # 30d 평균 거래량
                if or_end_ts not in df.index:
                    continue
                avg_vol_30d = df.loc[or_end_ts, "vol_sma30d"]
                if pd.isna(avg_vol_30d) or avg_vol_30d <= 0:
                    continue
                or_info = compute_or_strength(or_bars, avg_vol_30d)
                if or_info is None:
                    continue
                or_info["symbol"] = sym
                candidates.append(or_info)

            # 점수 상위 N개
            candidates.sort(key=lambda x: -x["score"])
            selected_today = candidates[:TOP_N_COINS]
            or_done = True

        # 포지션 관리 (intrabar SL/TP)
        for sym in list(positions.keys()):
            p = positions[sym]
            df = data[sym]
            if ts not in df.index:
                continue
            row = df.loc[ts]
            o, h, l = float(row["open"]), float(row["high"]), float(row["low"])
            p.update_high(h, l)

            # 펀딩
            if hour_utc in FUNDING_HOURS_UTC:
                cost = FUNDING_RATE * p.qty * p.entry
                equity -= cost
                p.funding_paid += cost

            # 시간 손절 (24h)
            if idx - p.entry_idx >= TIME_STOP_BARS:
                equity += p.close_qty(p.qty, o, "TIME_STOP", ts)
                p.duration_bars = idx - p.entry_idx
                trades.append(make_trade(p))
                cooldown_until[sym] = idx + COOLDOWN_AFTER_SL_BARS
                del positions[sym]
                continue

            sl_use = p.current_sl(idx)

            closed = False
            if p.side == "long":
                if o <= sl_use:
                    equity += p.close_qty(p.qty, o, "SL", ts)
                    p.duration_bars = idx - p.entry_idx
                    trades.append(make_trade(p))
                    cooldown_until[sym] = idx + COOLDOWN_AFTER_SL_BARS
                    del positions[sym]
                    closed = True
                    consec_stops += 1
                elif l <= sl_use:
                    reason = "TRAIL" if p.trailing_active(idx) else ("BE_SL" if p.be_active(idx) else "SL")
                    equity += p.close_qty(p.qty, sl_use, reason, ts)
                    p.duration_bars = idx - p.entry_idx
                    trades.append(make_trade(p))
                    cooldown_until[sym] = idx + COOLDOWN_AFTER_SL_BARS
                    del positions[sym]
                    closed = True
                    if reason == "SL":
                        consec_stops += 1
                    else:
                        consec_stops = 0
                if not closed:
                    for k in range(3):
                        if p.tp_done[k] or not p.open():
                            continue
                        if h >= p.tps[k]:
                            equity += p.close_qty(p.init_qty * TP_FRACTIONS[k], p.tps[k], f"TP{k+1}", ts)
                            p.tp_done[k] = True
                            if k == 0:
                                p.tp1_bar = idx
                            elif k == 1:
                                p.tp2_bar = idx
                            if not p.open():
                                p.duration_bars = idx - p.entry_idx
                                trades.append(make_trade(p))
                                cooldown_until[sym] = idx + COOLDOWN_AFTER_SL_BARS
                                del positions[sym]
                                consec_stops = 0
                                break
            else:  # short
                if o >= sl_use:
                    equity += p.close_qty(p.qty, o, "SL", ts)
                    p.duration_bars = idx - p.entry_idx
                    trades.append(make_trade(p))
                    cooldown_until[sym] = idx + COOLDOWN_AFTER_SL_BARS
                    del positions[sym]
                    closed = True
                    consec_stops += 1
                elif h >= sl_use:
                    reason = "TRAIL" if p.trailing_active(idx) else ("BE_SL" if p.be_active(idx) else "SL")
                    equity += p.close_qty(p.qty, sl_use, reason, ts)
                    p.duration_bars = idx - p.entry_idx
                    trades.append(make_trade(p))
                    cooldown_until[sym] = idx + COOLDOWN_AFTER_SL_BARS
                    del positions[sym]
                    closed = True
                    if reason == "SL":
                        consec_stops += 1
                    else:
                        consec_stops = 0
                if not closed:
                    for k in range(3):
                        if p.tp_done[k] or not p.open():
                            continue
                        if l <= p.tps[k]:
                            equity += p.close_qty(p.init_qty * TP_FRACTIONS[k], p.tps[k], f"TP{k+1}", ts)
                            p.tp_done[k] = True
                            if k == 0:
                                p.tp1_bar = idx
                            elif k == 1:
                                p.tp2_bar = idx
                            if not p.open():
                                p.duration_bars = idx - p.entry_idx
                                trades.append(make_trade(p))
                                cooldown_until[sym] = idx + COOLDOWN_AFTER_SL_BARS
                                del positions[sym]
                                consec_stops = 0
                                break

        # 일일 halt 체크
        daily_pnl_pct = (equity - daily_anchor_equity) / daily_anchor_equity if daily_anchor_equity > 0 else 0
        if daily_pnl_pct <= -DAILY_LOSS_HALT:
            halt_today = True
        if consec_stops >= CONSECUTIVE_STOPS:
            halt_today = True

        # 평가자산
        mtm = equity
        for sym, p in positions.items():
            df = data[sym]
            if ts in df.index:
                cl = float(df.loc[ts, "close"])
                if p.side == "long":
                    mtm += p.qty * (cl - p.entry)
                else:
                    mtm += p.qty * (p.entry - cl)
        curve.append({"time": ts, "equity": mtm})

        # 진입 시도 — OR 끝난 후 ~ ENTRY_WINDOW_END_HOUR 까지만
        if (not halt_today and or_done and selected_today
                and OR_HOURS < hour_utc < ENTRY_WINDOW_END_HOUR):
            for cand in selected_today:
                sym = cand["symbol"]
                if sym in positions:
                    continue
                if len(positions) >= MAX_CONCURRENT:
                    break
                if idx <= cooldown_until[sym]:
                    continue
                df = data[sym]
                # 현재 봉 까지의 최근 봉 (OR 종료 이후)
                or_end_ts = pd.Timestamp(current_day).tz_localize("UTC") if current_day.tz is None else current_day
                or_end_ts = or_end_ts + pd.Timedelta(hours=OR_HOURS - 1)
                recent_bars = df[(df.index > or_end_ts) & (df.index <= ts)]
                if len(recent_bars) < 2:
                    continue
                sig = detect_entry(cand["side"], cand, recent_bars, cand["atr"])
                if sig is None:
                    continue
                if idx + 1 >= len(master):
                    continue
                next_ts = master[idx + 1]
                if next_ts not in df.index:
                    continue
                next_open = float(df.loc[next_ts, "open"])
                setup = build_entry(cand["side"], cand, next_open, equity,
                                    pullback_extremum=sig.get("pullback_extremum"))
                if setup is None:
                    continue
                p = Position(
                    symbol=sym, side=cand["side"],
                    entry_idx=idx + 1, entry_time=next_ts,
                    entry_px=setup["entry"], sl=setup["sl"],
                    qty=setup["qty"], risk_usdt=setup["risk_usdt"],
                    or_high=cand["or_high"], or_low=cand["or_low"], atr=cand["atr"],
                )
                equity -= p.fees
                positions[sym] = p
                if sessions_with_trade == 0 or sessions_with_trade < sessions_total:
                    if sym not in [pp.symbol for pp in positions.values() if pp != p]:
                        # 세션당 한 번만 카운트
                        pass

    # 끝 — 미청산 강제 청산
    final_ts = master[-1] if master else None
    if final_ts is not None:
        for sym in list(positions.keys()):
            p = positions[sym]
            df = data[sym]
            if final_ts in df.index:
                raw_px = float(df.loc[final_ts, "close"])
            else:
                raw_px = p.entry
            equity += p.close_qty(p.qty, raw_px, "EOD", final_ts)
            p.duration_bars = len(master) - 1 - p.entry_idx
            trades.append(make_trade(p))
            del positions[sym]

    trades_df = pd.DataFrame(trades)
    curve_df = pd.DataFrame(curve)
    return summarize(equity, curve_df, trades_df, sessions_total), trades_df, curve_df


def summarize(equity, curve_df, trades_df, sessions_total):
    if trades_df.empty:
        return {"return": 0, "mdd": 0, "pf": float("nan"), "win_rate": 0,
                "trades": 0, "final_equity": equity, "sessions_total": sessions_total,
                "trades_per_session": 0,
                "long_trades": 0, "short_trades": 0, "max_losses": 0,
                "avg_win": 0, "avg_loss": 0, "avg_R": 0}
    wins = trades_df["pnl"] > 0
    gp = trades_df.loc[trades_df["pnl"] > 0, "pnl"].sum()
    gl = trades_df.loc[trades_df["pnl"] <= 0, "pnl"].sum()
    pf = gp / abs(gl) if gl < 0 else float("inf")
    losses = 0
    max_losses = 0
    for pnl in trades_df["pnl"]:
        if pnl <= 0:
            losses += 1
            max_losses = max(max_losses, losses)
        else:
            losses = 0
    side_counts = trades_df["side"].value_counts().to_dict()
    return {
        "return": equity / START_EQUITY - 1,
        "mdd": max_drawdown(curve_df["equity"].tolist()) if not curve_df.empty else 0,
        "pf": pf,
        "win_rate": wins.mean() * 100,
        "trades": len(trades_df),
        "sessions_total": sessions_total,
        "trades_per_session": len(trades_df) / sessions_total if sessions_total > 0 else 0,
        "long_trades": side_counts.get("long", 0),
        "short_trades": side_counts.get("short", 0),
        "max_losses": max_losses,
        "avg_win": trades_df.loc[trades_df["pnl"] > 0, "pnl"].mean() if wins.any() else 0,
        "avg_loss": trades_df.loc[trades_df["pnl"] <= 0, "pnl"].mean() if (~wins).any() else 0,
        "avg_R": trades_df["pnl_R"].mean(),
        "final_equity": equity,
    }


# =========================
# 실행
# =========================

if __name__ == "__main__":
    t0 = time.time()
    print(f"심볼 {len(SYMBOLS_POOL)}개 로딩...")
    data = load_data(SYMBOLS_POOL)
    print(f"\nORB-C v4 백테스트 시작 ({START} ~ {END})\n")

    summary, trades_df, curve_df = backtest(data)

    print("=" * 70)
    print("ORB-C v4 — 전구간 결과")
    print("=" * 70)
    pd.set_option("display.max_columns", 50)
    pd.set_option("display.width", 240)
    sdf = pd.DataFrame([summary])
    sdf["return"] = sdf["return"] * 100
    sdf["mdd"] = sdf["mdd"] * 100
    print(sdf.to_string(index=False))

    if not trades_df.empty:
        print("\n========== 심볼별 ==========")
        sym = trades_df.groupby("symbol").agg(
            trades=("pnl", "count"),
            pnl=("pnl", "sum"),
            win_rate=("pnl", lambda s: (s > 0).mean() * 100),
            avg_R=("pnl_R", "mean"),
            avg_mfe=("mfe_pct", "mean"),
            avg_mae=("mae_pct", "mean"),
        ).reset_index().sort_values("pnl", ascending=False)
        print(sym.to_string(index=False))

        print("\n========== LONG / SHORT ==========")
        side = trades_df.groupby("side").agg(
            trades=("pnl", "count"),
            pnl=("pnl", "sum"),
            win_rate=("pnl", lambda s: (s > 0).mean() * 100),
            avg_R=("pnl_R", "mean"),
        ).reset_index().sort_values("pnl", ascending=False)
        print(side.to_string(index=False))

        print("\n========== 종료 사유별 ==========")
        ex = trades_df.groupby("exit_reason").agg(
            trades=("pnl", "count"),
            pnl=("pnl", "sum"),
            avg_R=("pnl_R", "mean"),
            win_rate=("pnl", lambda s: (s > 0).mean() * 100),
        ).reset_index().sort_values("pnl", ascending=False)
        print(ex.to_string(index=False))

        print("\n========== 월별 ==========")
        curve_df["month"] = curve_df["time"].dt.tz_convert(None).dt.to_period("M")
        m = curve_df.groupby("month")["equity"].agg(["first", "last"])
        m["return_pct"] = (m["last"] / m["first"] - 1) * 100
        print(m.reset_index().to_string(index=False))

    print(f"\n실행 시간: {time.time() - t0:.1f}s")
