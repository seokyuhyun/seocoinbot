"""Pullback v3 — 4h 추세 되돌림 매수 전략.

설계 의도 (사용자 요구):
- 큰 수익보다 꾸준한 양수 (낮은 MDD, 높은 승률)
- 1일 1건 못 채워도 OK. 신호 품질 우선
- BTC/ETH/SOL 대형주만 (노이즈 적음)

전략 핵심:
- 1d 200 EMA 로 추세 확정 → 추세 방향으로만 진입 (역추세 금지)
- 4h 50 EMA 근처로 가격이 되돌림 했을 때 진입 (= 좋은 자리)
- RSI 과매도(롱) / 과매수(숏) + 직전 봉 반전 확인
- ATR 기반 SL, 1R/2R 분할 익절, TP1 후 BE 이동
- 리스크 1%/거래, 동시 3 포지션 최대

OOS 보호: 이 데이터셋은 이미 OOS 2회 사용 (SRT C1 + v0.1). 본 전략은
IS-only + walk-forward 로만 검증. 같은 OOS 재사용 안 함.
"""

from __future__ import annotations

import os
import sys
import time
import subprocess
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
CACHE_DIR = PROJECT_ROOT / "data" / "pullback_v3_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# =========================
# 설정
# =========================

START = "2025-01-01"
END = "2026-04-30"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

INTERVAL_TREND = "1d"
INTERVAL_ENTRY = "4h"

START_EQUITY = 10_000.0

# 비용
FEE_RATE = 0.0006              # 진입/청산 각각 0.06% (보수)
SLIPPAGE = 0.0002
FUNDING_RATE = 0.0001
FUNDING_HOURS_UTC = (0, 8, 16)

# 사이즈
RISK_PER_TRADE = 0.01          # 1%
LEVERAGE_CAP = 3.0
MAX_OPEN_POSITIONS = 3         # 3개 심볼 각각 1포지션

# 1d 추세 필터 (보수: 롱은 강한 상승장만, 숏은 더 강한 하락장만)
TREND_LONG_BUFFER = 0.02       # 1d close > 200EMA × 1.02
TREND_SHORT_BUFFER = 0.05      # 1d close < 200EMA × 0.95 (숏은 더 보수)

# 4h 진입 조건 (LONG)
LONG_PULLBACK_DEPTH = 0.015    # close 가 EMA50 -1.5% 이하 깊이까지 닿아야 함
LONG_PULLBACK_RECOVER = 0.005  # close 가 EMA50 +0.5% 위로는 안 가야 함
LONG_RSI_MIN = 25              # 너무 낮으면 진짜 폭락 → 패스
LONG_RSI_MAX = 45

# 4h 진입 조건 (SHORT)
SHORT_PULLBACK_DEPTH = 0.015
SHORT_PULLBACK_RECOVER = 0.005
SHORT_RSI_MIN = 55
SHORT_RSI_MAX = 75             # 너무 높으면 진짜 폭등 → 패스

MIN_ATR_PCT = 0.005            # 4h ATR/close >= 0.5%

# 손절
ATR_PERIOD = 14
ATR_SL_MULT = 2.0              # SL = ATR × 2 (4h 노이즈 흡수)
SL_MIN_PCT = 0.005             # 0.5% 미만은 강제 0.5%
SL_MAX_PCT = 0.05              # 5% 초과면 신호 무효

# 익절
TP_R_MULTIPLES = (1.0, 2.0)    # 1R, 2R
TP_FRACTIONS = (0.50, 0.50)    # 50%, 50%

# 시간·쿨다운·중단
COOLDOWN_BARS = 6              # 24h = 4h × 6
TIME_STOP_BARS = 42            # 7일 = 4h × 42
DAILY_LOSS_HALT = 0.03
CONSECUTIVE_STOP_HALT = 3

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
    if u == "m":
        return n * 60 * 1000
    if u == "h":
        return n * 60 * 60 * 1000
    if u == "d":
        return n * 24 * 60 * 60 * 1000
    raise ValueError("interval?")

def slip_open(price, side):
    return price * (1 + SLIPPAGE) if side == "long" else price * (1 - SLIPPAGE)

def slip_close(price, side):
    return price * (1 - SLIPPAGE) if side == "long" else price * (1 + SLIPPAGE)


# =========================
# 데이터 (캐시 우선)
# =========================

def fetch_klines(symbol, interval, start, end, warmup_days=400):
    """warmup 400일 = 1d EMA200 + 여유."""
    cache_path = CACHE_DIR / f"{symbol}_{interval}.csv"
    if cache_path.exists():
        df = pd.read_csv(cache_path)
        df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
        df = df.drop_duplicates("open_time").sort_values("open_time").set_index("open_time", drop=False)
        return df

    start_dt = dt_utc(start) - timedelta(days=warmup_days)
    end_dt = dt_utc(end) + timedelta(days=1)
    cur = ms(start_dt)
    end_ms = ms(end_dt)
    rows = []
    session = requests.Session()
    backoff = 0.15
    while cur < end_ms:
        r = session.get(f"{BINANCE_FAPI}/klines", params={
            "symbol": symbol, "interval": interval,
            "startTime": cur, "endTime": end_ms - 1, "limit": 1500,
        }, timeout=20)
        if r.status_code == 429:
            wait = min(60.0, backoff * 4)
            print(f"  [429] {symbol} {interval} backoff {wait:.1f}s")
            time.sleep(wait)
            backoff = wait
            continue
        if r.status_code != 200:
            raise RuntimeError(f"{symbol} {interval} {r.status_code}: {r.text[:200]}")
        data = r.json()
        if not data:
            break
        rows.extend(data)
        nxt = int(data[-1][0]) + interval_ms(interval)
        if nxt <= cur:
            break
        cur = nxt
        time.sleep(backoff)
    if not rows:
        raise RuntimeError(f"{symbol} {interval} 데이터 없음")
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

def ema(s, span):
    return s.ewm(span=span, adjust=False, min_periods=span).mean()

def rsi_wilder(close, length=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    ag = gain.ewm(alpha=1/length, adjust=False, min_periods=length).mean()
    al = loss.ewm(alpha=1/length, adjust=False, min_periods=length).mean()
    rs = ag / al.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)

def atr_wilder(df, length=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/length, adjust=False, min_periods=length).mean()


def prepare_4h(df):
    x = df.copy()
    x["ema50"] = ema(x["close"], 50)
    x["rsi"] = rsi_wilder(x["close"], 14)
    x["atr"] = atr_wilder(x, ATR_PERIOD)
    x["atr_pct"] = x["atr"] / x["close"]
    x["body_up"] = x["close"] > x["open"]    # 양봉
    x["body_dn"] = x["close"] < x["open"]    # 음봉
    return x


def prepare_1d(df):
    x = df.copy()
    x["ema200_1d"] = ema(x["close"], 200)
    x["close_1d"] = x["close"]
    # 1d 봉의 종료 시각 = open_time + 1d (캐시엔 open_time 만 있음)
    x["bar_close_time"] = x["open_time"] + pd.Timedelta(days=1)
    return x[["bar_close_time", "close_1d", "ema200_1d"]].copy()


def align_1d_to_4h(df4h, df1d):
    """look-ahead 차단: 4h 봉 close_time 이전에 이미 마감된 1d 값만 사용."""
    sig_times = df4h.index + pd.Timedelta(hours=4)  # 4h 봉 close_time
    h = df1d.set_index("bar_close_time").sort_index()
    close_1d = h["close_1d"].reindex(sig_times, method="ffill").to_numpy()
    ema200_1d = h["ema200_1d"].reindex(sig_times, method="ffill").to_numpy()
    return close_1d, ema200_1d


def load_all_data(symbols):
    data = {}
    for sym in symbols:
        print(f"다운로드: {sym}")
        df4h_raw = fetch_klines(sym, INTERVAL_ENTRY, START, END)
        df1d_raw = fetch_klines(sym, INTERVAL_TREND, START, END)
        df4h = prepare_4h(df4h_raw)
        df1d = prepare_1d(df1d_raw)
        c1d, e1d = align_1d_to_4h(df4h, df1d)
        df4h["close_1d"] = c1d
        df4h["ema200_1d"] = e1d
        data[sym] = df4h
        print(f"  {sym} 4h 봉: {len(df4h):,}")
    return data


# =========================
# 신호
# =========================

def long_signal(row, prev_row):
    """LONG 진입 조건. 모두 True 면 진입. 반환: (ok, reason_if_not)."""
    needed = ("close_1d", "ema200_1d", "close", "ema50", "rsi", "atr", "atr_pct")
    if any(pd.isna(row[c]) for c in needed):
        return False
    # 1) 1d 추세: close > 200EMA × 1.02
    if row["close_1d"] <= row["ema200_1d"] * (1 + TREND_LONG_BUFFER):
        return False
    # 2) 되돌림 깊이: 현재 봉 low 가 EMA50 -1.5% 이하 닿았어야 (= 진짜 pullback)
    if row["low"] > row["ema50"] * (1 - LONG_PULLBACK_DEPTH):
        return False
    # 3) 회복 위치: close 가 EMA50 위 +0.5% 안에서 마감 (= 반등 시작)
    if row["close"] > row["ema50"] * (1 + LONG_PULLBACK_RECOVER):
        return False
    # close 가 EMA50 아래 -1.5% 보다 더 깊으면 패스 (= 추세 깨졌을 수도)
    if row["close"] < row["ema50"] * (1 - LONG_PULLBACK_DEPTH):
        return False
    # 4) RSI 과매도 zone
    if not (LONG_RSI_MIN <= row["rsi"] <= LONG_RSI_MAX):
        return False
    # 5) 반전 확인: 현재 봉 양봉
    if not row["body_up"]:
        return False
    # 6) ATR%
    if row["atr_pct"] < MIN_ATR_PCT:
        return False
    return True


def short_signal(row, prev_row):
    needed = ("close_1d", "ema200_1d", "close", "ema50", "rsi", "atr", "atr_pct")
    if any(pd.isna(row[c]) for c in needed):
        return False
    # 1) 1d 추세: close < 200EMA × 0.95
    if row["close_1d"] >= row["ema200_1d"] * (1 - TREND_SHORT_BUFFER):
        return False
    # 2) 되돌림: high 가 EMA50 +1.5% 위로 닿았어야
    if row["high"] < row["ema50"] * (1 + SHORT_PULLBACK_DEPTH):
        return False
    # 3) close 가 EMA50 아래 -0.5% 안에서 마감
    if row["close"] < row["ema50"] * (1 - SHORT_PULLBACK_RECOVER):
        return False
    if row["close"] > row["ema50"] * (1 + SHORT_PULLBACK_DEPTH):
        return False
    # 4) RSI
    if not (SHORT_RSI_MIN <= row["rsi"] <= SHORT_RSI_MAX):
        return False
    # 5) 음봉
    if not row["body_dn"]:
        return False
    # 6) ATR%
    if row["atr_pct"] < MIN_ATR_PCT:
        return False
    return True


def build_entry(side, signal_row, next_open, equity):
    ent = slip_open(float(next_open), side)
    atr = float(signal_row["atr"])
    if not np.isfinite(ent) or not np.isfinite(atr):
        return None
    if side == "long":
        sl = ent - atr * ATR_SL_MULT
        if sl >= ent:
            return None
        sl_pct = (ent - sl) / ent
    else:
        sl = ent + atr * ATR_SL_MULT
        if sl <= ent:
            return None
        sl_pct = (sl - ent) / ent
    if sl_pct > SL_MAX_PCT:
        return None
    if sl_pct < SL_MIN_PCT:
        # 강제 0.5%
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
    return {"side": side, "entry": ent, "sl": sl, "qty": qty, "sl_pct": sl_pct,
            "risk_usdt": risk_usdt}


# =========================
# 포지션
# =========================

class Position:
    def __init__(self, symbol, side, entry_time, entry, sl, qty, sl_pct, risk_usdt, entry_idx):
        self.symbol = symbol
        self.side = side
        self.entry_time = entry_time
        self.entry_idx = entry_idx
        self.entry = float(entry)
        self.sl_initial = float(sl)
        self.sl = float(sl)
        self.qty = float(qty)
        self.init_qty = float(qty)
        self.sl_pct = sl_pct
        self.risk_usdt = risk_usdt

        r = abs(self.entry - self.sl_initial)
        if side == "long":
            self.tps = [self.entry + r * m for m in TP_R_MULTIPLES]
        else:
            self.tps = [self.entry - r * m for m in TP_R_MULTIPLES]
        self.tp_done = [False] * len(TP_R_MULTIPLES)
        self.tp1_bar = None

        self.gross_pnl = 0.0
        self.fees = self.entry * self.qty * FEE_RATE
        self.funding_paid = 0.0
        self.mae = 0.0
        self.mfe = 0.0
        self.exit_reason = None
        self.exit_time = None
        self.exit_price = None

    def open(self):
        return self.qty > 1e-12

    def be_active(self, idx):
        return self.tp_done[0] and self.tp1_bar is not None and idx > self.tp1_bar

    def update_mfe_mae(self, h, l):
        if self.side == "long":
            self.mfe = max(self.mfe, h / self.entry - 1)
            self.mae = min(self.mae, l / self.entry - 1)
        else:
            self.mfe = max(self.mfe, self.entry / l - 1)
            self.mae = min(self.mae, self.entry / h - 1)

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
        "sl_pct": p.sl_pct * 100,
        "pnl": p.net_pnl(),
        "pnl_R": p.net_pnl() / p.risk_usdt if p.risk_usdt > 0 else 0,
        "fees": p.fees,
        "funding": p.funding_paid,
        "mae_pct": p.mae * 100,
        "mfe_pct": p.mfe * 100,
        "duration_bars": None,  # filled later
    }


# =========================
# 백테스트
# =========================

def max_drawdown(curve):
    if len(curve) == 0:
        return 0.0
    s = pd.Series(curve)
    return float((s / s.cummax() - 1).min())


def backtest(data, start_date=None, end_date=None):
    """data 는 SYMBOLS 별 4h DataFrame.
    start_date/end_date 로 IS/walk-forward fold 한정 가능."""
    btc = data["BTCUSDT"]
    start_ts = pd.Timestamp(dt_utc(start_date or START))
    end_ts = pd.Timestamp(dt_utc(end_date or END) + timedelta(days=1))
    master_times = btc.index[(btc.index >= start_ts) & (btc.index < end_ts)].tolist()

    equity = START_EQUITY
    curve = []
    positions = {}
    trades = []
    pending = []  # [(sym, side, signal_row)]
    cooldown_until_idx = {s: -1 for s in data.keys()}

    # 일일 halt 추적
    consecutive_stops = 0
    halt_today = False
    current_utc_day = None
    daily_anchor_equity = equity

    signal_count = 0
    rejected_stop = 0
    rejected_position_cap = 0
    rejected_cooldown = 0
    rejected_halt = 0

    for idx in range(1, len(master_times) - 1):
        ts = master_times[idx]
        next_ts = master_times[idx + 1]

        # 새 UTC 일이면 daily 리셋
        day = ts.normalize()
        if current_utc_day is None or day != current_utc_day:
            current_utc_day = day
            halt_today = False
            consecutive_stops = 0
            daily_anchor_equity = equity

        # 1) pending 신호 → 이 봉 OPEN 에서 진입
        if pending and not halt_today:
            for sig in pending:
                sym = sig["symbol"]
                if sym in positions:
                    continue
                if idx <= cooldown_until_idx[sym]:
                    rejected_cooldown += 1
                    continue
                if len(positions) >= MAX_OPEN_POSITIONS:
                    rejected_position_cap += 1
                    continue
                df = data[sym]
                if ts not in df.index:
                    continue
                entry_row = df.loc[ts]
                setup = build_entry(sig["side"], sig["signal_row"],
                                    entry_row["open"], equity)
                if setup is None:
                    rejected_stop += 1
                    continue
                p = Position(
                    symbol=sym, side=setup["side"],
                    entry_time=ts, entry_idx=idx,
                    entry=setup["entry"], sl=setup["sl"],
                    qty=setup["qty"], sl_pct=setup["sl_pct"],
                    risk_usdt=setup["risk_usdt"],
                )
                equity -= p.fees
                positions[sym] = p
        pending = []

        # 2) 보유 포지션 관리
        for sym in list(positions.keys()):
            p = positions[sym]
            df = data[sym]
            if ts not in df.index:
                continue
            row = df.loc[ts]
            o, h, l = float(row["open"]), float(row["high"]), float(row["low"])
            p.update_mfe_mae(h, l)

            # 펀딩 (4h 봉이면 봉 시작 시각 hour 가 funding hour 일 때만 적용)
            if ts.hour in FUNDING_HOURS_UTC and ts.minute == 0:
                cost = FUNDING_RATE * p.qty * p.entry
                equity -= cost
                p.funding_paid += cost

            # 시간 손절
            if idx - p.entry_idx >= TIME_STOP_BARS:
                px_use = o
                equity += p.close_qty(p.qty, px_use, "TIME_STOP", ts)
                p.duration_bars = idx - p.entry_idx
                trades.append(make_trade(p))
                trades[-1]["duration_bars"] = p.duration_bars
                cooldown_until_idx[sym] = idx + COOLDOWN_BARS
                del positions[sym]
                continue

            active_sl = p.entry if p.be_active(idx) else p.sl_initial
            closed = False

            if p.side == "long":
                # SL 먼저 (보수)
                if o <= active_sl:
                    equity += p.close_qty(p.qty, o, "SL", ts)
                    p.duration_bars = idx - p.entry_idx
                    trades.append(make_trade(p))
                    trades[-1]["duration_bars"] = p.duration_bars
                    cooldown_until_idx[sym] = idx + COOLDOWN_BARS
                    del positions[sym]
                    closed = True
                    consecutive_stops += 1
                elif l <= active_sl:
                    reason = "BE_SL" if p.be_active(idx) else "SL"
                    equity += p.close_qty(p.qty, active_sl, reason, ts)
                    p.duration_bars = idx - p.entry_idx
                    trades.append(make_trade(p))
                    trades[-1]["duration_bars"] = p.duration_bars
                    cooldown_until_idx[sym] = idx + COOLDOWN_BARS
                    del positions[sym]
                    closed = True
                    if reason == "SL":
                        consecutive_stops += 1
                    else:
                        consecutive_stops = 0  # BE = no loss
                if not closed:
                    # TPs
                    for k in range(len(TP_R_MULTIPLES)):
                        if p.tp_done[k] or not p.open():
                            continue
                        if h >= p.tps[k]:
                            equity += p.close_qty(p.init_qty * TP_FRACTIONS[k], p.tps[k], f"TP{k+1}", ts)
                            p.tp_done[k] = True
                            if k == 0:
                                p.tp1_bar = idx
                            if not p.open():
                                p.duration_bars = idx - p.entry_idx
                                trades.append(make_trade(p))
                                trades[-1]["duration_bars"] = p.duration_bars
                                cooldown_until_idx[sym] = idx + COOLDOWN_BARS
                                del positions[sym]
                                consecutive_stops = 0
                                break
            else:  # short
                if o >= active_sl:
                    equity += p.close_qty(p.qty, o, "SL", ts)
                    p.duration_bars = idx - p.entry_idx
                    trades.append(make_trade(p))
                    trades[-1]["duration_bars"] = p.duration_bars
                    cooldown_until_idx[sym] = idx + COOLDOWN_BARS
                    del positions[sym]
                    closed = True
                    consecutive_stops += 1
                elif h >= active_sl:
                    reason = "BE_SL" if p.be_active(idx) else "SL"
                    equity += p.close_qty(p.qty, active_sl, reason, ts)
                    p.duration_bars = idx - p.entry_idx
                    trades.append(make_trade(p))
                    trades[-1]["duration_bars"] = p.duration_bars
                    cooldown_until_idx[sym] = idx + COOLDOWN_BARS
                    del positions[sym]
                    closed = True
                    if reason == "SL":
                        consecutive_stops += 1
                    else:
                        consecutive_stops = 0
                if not closed:
                    for k in range(len(TP_R_MULTIPLES)):
                        if p.tp_done[k] or not p.open():
                            continue
                        if l <= p.tps[k]:
                            equity += p.close_qty(p.init_qty * TP_FRACTIONS[k], p.tps[k], f"TP{k+1}", ts)
                            p.tp_done[k] = True
                            if k == 0:
                                p.tp1_bar = idx
                            if not p.open():
                                p.duration_bars = idx - p.entry_idx
                                trades.append(make_trade(p))
                                trades[-1]["duration_bars"] = p.duration_bars
                                cooldown_until_idx[sym] = idx + COOLDOWN_BARS
                                del positions[sym]
                                consecutive_stops = 0
                                break

        # 일일 halt 체크
        daily_pnl_pct = (equity - daily_anchor_equity) / daily_anchor_equity
        if daily_pnl_pct <= -DAILY_LOSS_HALT:
            halt_today = True
        if consecutive_stops >= CONSECUTIVE_STOP_HALT:
            halt_today = True

        # 3) 평가자산
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

        # 4) 신호 평가 → 다음 봉 OPEN 진입 펜딩
        if halt_today:
            continue
        for sym, df in data.items():
            if sym in positions:
                continue
            if idx <= cooldown_until_idx[sym]:
                continue
            if ts not in df.index or next_ts not in df.index:
                continue
            row = df.loc[ts]
            prev_row = df.loc[master_times[idx - 1]] if master_times[idx - 1] in df.index else row
            if long_signal(row, prev_row):
                signal_count += 1
                pending.append({"symbol": sym, "side": "long", "signal_row": row.copy()})
            elif short_signal(row, prev_row):
                signal_count += 1
                pending.append({"symbol": sym, "side": "short", "signal_row": row.copy()})

    # 종료: 미청산 강제 청산
    final_ts = master_times[-1]
    for sym in list(positions.keys()):
        p = positions[sym]
        df = data[sym]
        raw_px = float(df.loc[final_ts, "close"]) if final_ts in df.index else p.entry
        equity += p.close_qty(p.qty, raw_px, "EOD", final_ts)
        p.duration_bars = len(master_times) - 1 - p.entry_idx
        trades.append(make_trade(p))
        trades[-1]["duration_bars"] = p.duration_bars
        del positions[sym]

    trades_df = pd.DataFrame(trades)
    curve_df = pd.DataFrame(curve)
    return summarize(equity, curve_df, trades_df, signal_count,
                     rejected_stop, rejected_position_cap,
                     rejected_cooldown, rejected_halt), trades_df, curve_df


def summarize(equity, curve_df, trades_df, sig, rs, rp, rc, rh):
    if trades_df.empty:
        return {"return": 0, "mdd": 0, "pf": np.nan, "win_rate": 0,
                "trades": 0, "final_equity": equity,
                "signal_count": sig, "rejected_stop": rs,
                "rejected_position_cap": rp, "rejected_cooldown": rc,
                "rejected_halt": rh,
                "long_trades": 0, "short_trades": 0,
                "max_losses": 0, "avg_win": 0, "avg_loss": 0, "avg_R": 0}
    wins = trades_df["pnl"] > 0
    gp = trades_df.loc[trades_df["pnl"] > 0, "pnl"].sum()
    gl = trades_df.loc[trades_df["pnl"] <= 0, "pnl"].sum()
    pf = gp / abs(gl) if gl < 0 else np.inf
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
        "long_trades": side_counts.get("long", 0),
        "short_trades": side_counts.get("short", 0),
        "max_losses": max_losses,
        "avg_win": trades_df.loc[trades_df["pnl"] > 0, "pnl"].mean() if wins.any() else 0,
        "avg_loss": trades_df.loc[trades_df["pnl"] <= 0, "pnl"].mean() if (~wins).any() else 0,
        "avg_R": trades_df["pnl_R"].mean() if "pnl_R" in trades_df.columns else 0,
        "signal_count": sig,
        "rejected_stop": rs,
        "rejected_position_cap": rp,
        "rejected_cooldown": rc,
        "rejected_halt": rh,
        "final_equity": equity,
    }


# =========================
# 실행
# =========================

if __name__ == "__main__":
    t0 = time.time()
    data = load_all_data(SYMBOLS)

    summary, trades_df, curve_df = backtest(data)

    print("\n" + "=" * 60)
    print("Pullback v3 — 4h 추세 되돌림 매수 (전구간)")
    print("=" * 60)
    pd.set_option("display.max_columns", 50)
    pd.set_option("display.width", 220)
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

        print("\n========== LONG/SHORT ==========")
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

        print("\n========== 최근 10건 ==========")
        cols = ["symbol", "side", "entry_time", "exit_time", "exit_reason",
                "sl_pct", "pnl", "pnl_R", "mfe_pct", "mae_pct"]
        print(trades_df[cols].tail(10).to_string(index=False))

    print(f"\n실행 시간: {(time.time() - t0):.1f}s")
