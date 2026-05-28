import sys, subprocess, time, math, os
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

# 캐시 디렉토리 — 한 번 다운로드 후 재사용. 강제 갱신은 폴더 삭제
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "data" / "breakout_v2_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# stdout UTF-8 + surrogate replace (Claude Code API 호환)
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

# =========================
# 기본 설정
# =========================

START = "2025-01-01"
END = "2026-04-30"

INTERVAL_ENTRY = "15m"
INTERVAL_TREND = "1h"

START_EQUITY = 10_000.0

AUTO_SYMBOLS = True
TOP_N = 20          # 원문처럼 50~100개 테스트하려면 50 또는 100으로 변경
MANUAL_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT",
]

STRICT_ALL = True   # True: 원문 조건 모두 만족. False: 점수 8점 이상이면 진입
MIN_SCORE = 8

# BTC 는 추세 필터로 항상 사용. True 면 BTC 자체는 매매 안 함 (다른 알트만 매매).
EXCLUDE_BTC_FROM_TRADES = True

RISK_PER_TRADE = 0.005      # 계좌의 0.5%
LEVERAGE_CAP = 3.0          # 명목 포지션 최대 equity * 3배
MAX_OPEN_POSITIONS = 8      # 원문에는 없지만 포트폴리오 과노출 방지용. 원문 그대로면 999로 변경

FEE_RATE = 0.0006           # 진입/청산 각각 0.06%
SLIPPAGE = 0.0002           # 진입/청산 각각 0.02%

BTC_EMA_BUFFER = 0.003      # BTC EMA200 ±0.3% 이내 무거래
VOL_MULT = 1.5
ATR_MIN_PCT = 0.0035
ATR_SL_MULT = 1.3
MIN_STOP_PCT = 0.004
MAX_STOP_PCT = 0.025

COOLDOWN_BARS = 16          # 4시간 = 15분봉 16개

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
    raise ValueError("지원하지 않는 interval")

def entry_price(price, side):
    if side == "long":
        return float(price) * (1 + SLIPPAGE)
    return float(price) * (1 - SLIPPAGE)

def exit_price(price, side):
    if side == "long":
        return float(price) * (1 - SLIPPAGE)
    return float(price) * (1 + SLIPPAGE)


# =========================
# 심볼 자동 선정
# =========================

def get_top_symbols(top_n=20):
    session = requests.Session()

    info = session.get(f"{BINANCE_FAPI}/exchangeInfo", timeout=20).json()
    tickers = session.get(f"{BINANCE_FAPI}/ticker/24hr", timeout=20).json()

    start_cutoff = ms(dt_utc(START) - timedelta(days=30))

    valid = {}
    for s in info["symbols"]:
        sym = s.get("symbol")
        if (
            s.get("contractType") == "PERPETUAL"
            and s.get("quoteAsset") == "USDT"
            and s.get("status") == "TRADING"
            and s.get("onboardDate", 0) <= start_cutoff
        ):
            valid[sym] = True

    rows = []
    for t in tickers:
        sym = t.get("symbol")
        if sym in valid:
            try:
                qv = float(t.get("quoteVolume", 0))
            except Exception:
                qv = 0
            rows.append((sym, qv))

    rows = sorted(rows, key=lambda x: x[1], reverse=True)
    symbols = [s for s, _ in rows[:top_n]]

    if "BTCUSDT" not in symbols:
        symbols = ["BTCUSDT"] + symbols

    symbols = list(dict.fromkeys(symbols))
    print("자동 선정 심볼:", symbols)
    return symbols


# =========================
# 데이터 다운로드
# =========================

def fetch_klines(symbol, interval, start, end, warmup_days=80):
    """캐시 우선. 없으면 Binance API 에서 받아 저장."""
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
    backoff = 0.1   # 초기 sleep — 429 만나면 지수증가

    while cur < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": cur,
            "endTime": end_ms - 1,
            "limit": 1500,
        }

        r = session.get(f"{BINANCE_FAPI}/klines", params=params, timeout=20)

        if r.status_code == 429:
            # rate-limit: 지수 백오프 후 재시도 (이 batch 만)
            wait = min(60.0, backoff * 4)
            print(f"  [429] {symbol} {interval} backoff {wait:.1f}s")
            time.sleep(wait)
            backoff = wait
            continue

        if r.status_code != 200:
            raise RuntimeError(f"{symbol} {interval} API 오류: {r.status_code}, {r.text[:200]}")

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

    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore"
    ]

    df = pd.DataFrame(rows, columns=cols)

    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.drop_duplicates("open_time").sort_values("open_time")

    # 캐시 저장 (필요한 컬럼만)
    keep = ["open_time", "open", "high", "low", "close", "volume"]
    df[keep].to_csv(cache_path, index=False)

    df = df.set_index("open_time", drop=False)
    return df


# =========================
# 지표
# =========================

def rsi_wilder(close, length=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    ag = gain.ewm(alpha=1/length, adjust=False, min_periods=length).mean()
    al = loss.ewm(alpha=1/length, adjust=False, min_periods=length).mean()

    rs = ag / al.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)

def atr_wilder(df, length=14):
    high = df["high"]
    low = df["low"]
    close = df["close"]

    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1
    ).max(axis=1)

    return tr.ewm(alpha=1/length, adjust=False, min_periods=length).mean()

def adx_wilder(df, length=14):
    """표준 ADX (Wilder smoothing). regime 분류용."""
    high = df["high"]
    low = df["low"]
    close = df["close"]

    prev_close = close.shift()
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=high.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=high.index,
    )

    alpha = 1.0 / length
    atr_s = tr.ewm(alpha=alpha, adjust=False, min_periods=length).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=alpha, adjust=False, min_periods=length).mean() / atr_s
    minus_di = 100.0 * minus_dm.ewm(alpha=alpha, adjust=False, min_periods=length).mean() / atr_s
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=alpha, adjust=False, min_periods=length).mean()

def add_15m_indicators(df):
    x = df.copy()

    x["ema20"] = x["close"].ewm(span=20, adjust=False, min_periods=20).mean()
    x["ema50"] = x["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    x["ema200"] = x["close"].ewm(span=200, adjust=False, min_periods=200).mean()

    x["rsi"] = rsi_wilder(x["close"], 14)
    x["atr"] = atr_wilder(x, 14)
    x["atr_pct"] = x["atr"] / x["close"]

    x["vol_sma20"] = x["volume"].rolling(20, min_periods=20).mean()

    x["prev_high20"] = x["high"].shift(1).rolling(20, min_periods=20).max()
    x["prev_low20"] = x["low"].shift(1).rolling(20, min_periods=20).min()

    x["swing_low10"] = x["low"].rolling(10, min_periods=10).min()
    x["swing_high10"] = x["high"].rolling(10, min_periods=10).max()

    return x

def add_1h_indicators(df):
    x = df.copy()
    x["ema200_1h"] = x["close"].ewm(span=200, adjust=False, min_periods=200).mean()
    x["ema50_1h"] = x["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    # 5봉(=5시간) 기준 EMA50 기울기 (상대값)
    x["ema50_slope_1h"] = (x["ema50_1h"] - x["ema50_1h"].shift(5)) / x["ema50_1h"].shift(5)
    x["adx_1h"] = adx_wilder(x, 14)
    return x

def align_1h_to_15m(df15, df1h):
    signal_times = df15.index + pd.Timedelta(minutes=15)

    h = df1h.copy()
    h.index = h.index + pd.Timedelta(hours=1)

    out = {}
    for src_col in ["close", "ema200_1h", "ema50_slope_1h", "adx_1h"]:
        out[src_col] = h[src_col].reindex(signal_times, method="ffill").to_numpy()

    return out["close"], out["ema200_1h"], out["ema50_slope_1h"], out["adx_1h"]


# =========================
# 데이터 준비
# =========================

def load_all_data(symbols):
    data = {}

    for sym in symbols:
        try:
            print(f"다운로드: {sym}")
            df15 = fetch_klines(sym, INTERVAL_ENTRY, START, END)
            df1h = fetch_klines(sym, INTERVAL_TREND, START, END)

            df15 = add_15m_indicators(df15)
            df1h = add_1h_indicators(df1h)

            c1h, e1h, slope_1h, adx_1h = align_1h_to_15m(df15, df1h)
            df15["close_1h"] = c1h
            df15["ema200_1h"] = e1h
            df15["ema50_slope_1h"] = slope_1h
            df15["adx_1h"] = adx_1h

            data[sym] = df15
            print(f"{sym} 15m 캔들: {len(df15):,}")

        except Exception as e:
            print(f"{sym} 제외: {e}")

    if "BTCUSDT" not in data:
        raise RuntimeError("BTCUSDT 데이터가 반드시 필요합니다.")

    return data


# =========================
# 포지션
# =========================

class Position:
    def __init__(self, symbol, side, entry_time, entry, sl, qty, score, risk_usdt, regime="?"):
        self.symbol = symbol
        self.side = side
        self.entry_time = entry_time
        self.entry = float(entry)
        self.sl_initial = float(sl)
        self.sl = float(sl)

        self.qty = float(qty)
        self.init_qty = float(qty)

        self.score = score
        self.risk_usdt = risk_usdt
        self.regime = regime

        self.tp1_done = False
        self.tp2_done = False
        self.tp3_done = False
        self.tp1_bar = None

        r = abs(self.entry - self.sl_initial)

        if side == "long":
            self.tp1 = self.entry + r
            self.tp2 = self.entry + 2 * r
            self.tp3 = self.entry + 3 * r
        else:
            self.tp1 = self.entry - r
            self.tp2 = self.entry - 2 * r
            self.tp3 = self.entry - 3 * r

        self.gross_pnl = 0.0
        self.fees = self.entry * self.qty * FEE_RATE

        self.mae = 0.0
        self.mfe = 0.0
        self.exit_reason = None
        self.exit_time = None
        self.exit_price = None

    def open(self):
        return self.qty > 1e-12

    def be_active(self, bar_i):
        return self.tp1_done and self.tp1_bar is not None and bar_i > self.tp1_bar

    def update_mfe_mae(self, high, low):
        if self.side == "long":
            fav = high / self.entry - 1
            adv = low / self.entry - 1
        else:
            fav = self.entry / low - 1
            adv = self.entry / high - 1

        self.mfe = max(self.mfe, fav)
        self.mae = min(self.mae, adv)

    def close_qty(self, qty, raw_price, reason, ts):
        qty = min(qty, self.qty)
        if qty <= 0:
            return 0.0

        px = exit_price(raw_price, self.side)

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
        return self.gross_pnl - self.fees


def make_trade(p):
    return {
        "symbol": p.symbol,
        "side": p.side,
        "regime": p.regime,
        "entry_time": p.entry_time,
        "exit_time": p.exit_time,
        "exit_reason": p.exit_reason,
        "entry": p.entry,
        "exit_price": p.exit_price,
        "score": p.score,
        "risk_usdt": p.risk_usdt,
        "pnl": p.net_pnl(),
        "fees": p.fees,
        "mae_pct": p.mae * 100,
        "mfe_pct": p.mfe * 100,
    }


# =========================
# 신호
# =========================

def btc_market_state(btc_row):
    c = btc_row["close_1h"]
    e = btc_row["ema200_1h"]

    if not np.isfinite(c) or not np.isfinite(e):
        return "neutral"

    if c > e * (1 + BTC_EMA_BUFFER):
        return "up"

    if c < e * (1 - BTC_EMA_BUFFER):
        return "down"

    return "neutral"


# Regime 분류 임계값 — BTC 1h 기준
REGIME_ADX_STRONG = 25.0
REGIME_ADX_RANGE = 20.0
REGIME_SLOPE_RISING = 0.001    # 5봉 동안 +0.1% 이상 상승
REGIME_SLOPE_FALLING = -0.001
REGIME_EMA_BUFFER = 0.005       # ±0.5% 위/아래

def classify_regime(btc_row):
    """BTC 1h 기준 5단계 regime: STRONG_UP / WEAK_UP / RANGE / WEAK_DOWN / STRONG_DOWN.

    근거: EMA200 대비 가격, EMA50 5봉 기울기, ADX(14). 셋 다 일치 = STRONG,
    가격·기울기 일치 = WEAK, ADX 약하면서 가격 EMA200 근처 = RANGE.
    """
    c = btc_row["close_1h"]
    e200 = btc_row["ema200_1h"]
    slope = btc_row["ema50_slope_1h"]
    adx = btc_row["adx_1h"]

    if not (np.isfinite(c) and np.isfinite(e200) and np.isfinite(slope) and np.isfinite(adx)):
        return "RANGE"   # 데이터 미확보면 안전쪽

    above_200 = c > e200 * (1 + REGIME_EMA_BUFFER)
    below_200 = c < e200 * (1 - REGIME_EMA_BUFFER)
    rising = slope > REGIME_SLOPE_RISING
    falling = slope < REGIME_SLOPE_FALLING
    strong = adx > REGIME_ADX_STRONG
    rangy = adx < REGIME_ADX_RANGE

    # 강한 추세 — 가격·기울기·ADX 셋 다 일치
    if above_200 and rising and strong:
        return "STRONG_UP"
    if below_200 and falling and strong:
        return "STRONG_DOWN"
    # 약한 추세 — 가격·기울기 일치 (ADX 무관)
    if above_200 and rising:
        return "WEAK_UP"
    if below_200 and falling:
        return "WEAK_DOWN"
    # 횡보 — ADX 약하고 가격 EMA200 근처
    if rangy and abs(c - e200) / e200 < REGIME_EMA_BUFFER:
        return "RANGE"
    # 그 외: 가격이 한쪽에 있으면 약한 방향, 아니면 RANGE
    if above_200:
        return "WEAK_UP"
    if below_200:
        return "WEAK_DOWN"
    return "RANGE"

def score_signal(row, market_state, side):
    c1 = row["close_1h"]
    e1 = row["ema200_1h"]

    close = row["close"]
    ema20 = row["ema20"]
    ema50 = row["ema50"]

    rsi = row["rsi"]
    atr_pct = row["atr_pct"]

    volume_ok = row["volume"] >= row["vol_sma20"] * VOL_MULT
    atr_ok = atr_pct >= ATR_MIN_PCT

    if side == "long":
        btc_ok = market_state == "up"
        coin_1h_ok = c1 > e1
        ema_ok = ema20 > ema50
        breakout_ok = close > row["prev_high20"]
        rsi_ok = 52 <= rsi <= 72

    else:
        btc_ok = market_state == "down"
        coin_1h_ok = c1 < e1
        ema_ok = ema20 < ema50
        breakout_ok = close < row["prev_low20"]
        rsi_ok = 28 <= rsi <= 48

    score = 0
    score += 2 if btc_ok else 0
    score += 2 if coin_1h_ok else 0
    score += 2 if ema_ok else 0
    score += 2 if breakout_ok else 0
    score += 2 if volume_ok else 0
    score += 1 if rsi_ok else 0
    score += 1 if atr_ok else 0

    checks = {
        "btc_ok": btc_ok,
        "coin_1h_ok": coin_1h_ok,
        "ema_ok": ema_ok,
        "breakout_ok": breakout_ok,
        "volume_ok": volume_ok,
        "rsi_ok": rsi_ok,
        "atr_ok": atr_ok,
    }

    return score, checks

def raw_signal(row, market_state, strict_mode=None, min_score=None):
    """strict_mode/min_score 가 None 이면 모듈 상수 사용. regime 별로 다를 때 override."""
    if strict_mode is None:
        strict_mode = STRICT_ALL
    if min_score is None:
        min_score = MIN_SCORE

    long_score, long_checks = score_signal(row, market_state, "long")
    short_score, short_checks = score_signal(row, market_state, "short")

    if strict_mode:
        long_ok = all(long_checks.values())
        short_ok = all(short_checks.values())
    else:
        long_ok = long_score >= min_score and long_checks["btc_ok"]
        short_ok = short_score >= min_score and short_checks["btc_ok"]

    if long_ok and not short_ok:
        return "long", long_score

    if short_ok and not long_ok:
        return "short", short_score

    return None, 0

def build_entry(symbol, side, score, signal_row, next_open, equity, risk_pct=None):
    if risk_pct is None:
        risk_pct = RISK_PER_TRADE
    ent = entry_price(next_open, side)
    atr = signal_row["atr"]

    if not np.isfinite(ent) or not np.isfinite(atr):
        return None

    if side == "long":
        sl_atr = ent - atr * ATR_SL_MULT
        sl_swing = signal_row["swing_low10"]
        sl = min(sl_atr, sl_swing)

        if not np.isfinite(sl) or sl >= ent:
            return None

        stop_pct = (ent - sl) / ent

    else:
        sl_atr = ent + atr * ATR_SL_MULT
        sl_swing = signal_row["swing_high10"]
        sl = max(sl_atr, sl_swing)

        if not np.isfinite(sl) or sl <= ent:
            return None

        stop_pct = (sl - ent) / ent

    if stop_pct < MIN_STOP_PCT or stop_pct > MAX_STOP_PCT:
        return None

    risk_usdt = equity * risk_pct
    qty = risk_usdt / abs(ent - sl)

    max_notional = equity * LEVERAGE_CAP
    notional = qty * ent

    if notional > max_notional:
        qty = max_notional / ent

    if qty <= 0:
        return None

    return {
        "symbol": symbol,
        "side": side,
        "entry": ent,
        "sl": sl,
        "qty": qty,
        "score": score,
        "risk_usdt": risk_usdt,
        "stop_pct": stop_pct,
    }


# =========================
# 백테스트
# =========================

def max_drawdown(curve):
    if len(curve) == 0:
        return 0.0

    s = pd.Series(curve)
    return float((s / s.cummax() - 1).min())

def backtest(data):
    btc = data["BTCUSDT"]

    start_ts = pd.Timestamp(dt_utc(START))
    end_ts = pd.Timestamp(dt_utc(END) + timedelta(days=1))

    master_times = btc.index[(btc.index >= start_ts) & (btc.index < end_ts)].tolist()

    equity = START_EQUITY
    curve = []
    positions = {}
    trades = []
    pending = []
    cooldown = {s: pd.Timestamp("1970-01-01", tz="UTC") for s in data.keys()}

    signal_count = 0
    rejected_stop = 0
    rejected_position_cap = 0
    rejected_cooldown = 0

    for idx in range(1, len(master_times) - 1):
        ts = master_times[idx]
        next_ts = master_times[idx + 1]

        # 1) 이전 봉 신호를 현재 봉 시가에 진입
        if pending:
            pending = sorted(pending, key=lambda x: x["score"], reverse=True)

            for sig in pending:
                sym = sig["symbol"]

                if sym in positions:
                    continue

                if ts < cooldown[sym]:
                    rejected_cooldown += 1
                    continue

                if len(positions) >= MAX_OPEN_POSITIONS:
                    rejected_position_cap += 1
                    continue

                df = data[sym]

                if ts not in df.index:
                    continue

                entry_row = df.loc[ts]
                signal_row = sig["signal_row"]

                setup = build_entry(
                    symbol=sym,
                    side=sig["side"],
                    score=sig["score"],
                    signal_row=signal_row,
                    next_open=entry_row["open"],
                    equity=equity,
                    risk_pct=sig.get("risk_pct"),
                )

                if setup is None:
                    rejected_stop += 1
                    continue

                p = Position(
                    symbol=sym,
                    side=setup["side"],
                    entry_time=ts,
                    entry=setup["entry"],
                    sl=setup["sl"],
                    qty=setup["qty"],
                    score=setup["score"],
                    risk_usdt=setup["risk_usdt"],
                    regime=sig.get("regime", "?"),
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
            o = row["open"]
            h = row["high"]
            l = row["low"]

            p.update_mfe_mae(h, l)

            closed = False

            # TP1 이후 손절은 다음 봉부터 진입가로 이동
            active_sl = p.entry if p.be_active(idx) else p.sl_initial

            if p.side == "long":
                sl_hit_open = o <= active_sl
                sl_hit_intra = l <= active_sl

                # 보수적: SL 먼저
                if sl_hit_open:
                    equity += p.close_qty(p.qty, o, "SL", ts)
                    trades.append(make_trade(p))
                    cooldown[sym] = ts + pd.Timedelta(hours=4)
                    del positions[sym]
                    closed = True

                elif sl_hit_intra:
                    reason = "BE_SL" if p.be_active(idx) else "SL"
                    equity += p.close_qty(p.qty, active_sl, reason, ts)
                    trades.append(make_trade(p))
                    cooldown[sym] = ts + pd.Timedelta(hours=4)
                    del positions[sym]
                    closed = True

                if not closed:
                    if not p.tp1_done and h >= p.tp1:
                        equity += p.close_qty(p.init_qty * 0.40, p.tp1, "TP1_PART", ts)
                        p.tp1_done = True
                        p.tp1_bar = idx

                    if p.open() and not p.tp2_done and h >= p.tp2:
                        equity += p.close_qty(p.init_qty * 0.30, p.tp2, "TP2_PART", ts)
                        p.tp2_done = True

                    if p.open() and h >= p.tp3:
                        equity += p.close_qty(p.qty, p.tp3, "TP3", ts)
                        trades.append(make_trade(p))
                        cooldown[sym] = ts + pd.Timedelta(hours=4)
                        del positions[sym]
                        closed = True

            else:
                sl_hit_open = o >= active_sl
                sl_hit_intra = h >= active_sl

                if sl_hit_open:
                    equity += p.close_qty(p.qty, o, "SL", ts)
                    trades.append(make_trade(p))
                    cooldown[sym] = ts + pd.Timedelta(hours=4)
                    del positions[sym]
                    closed = True

                elif sl_hit_intra:
                    reason = "BE_SL" if p.be_active(idx) else "SL"
                    equity += p.close_qty(p.qty, active_sl, reason, ts)
                    trades.append(make_trade(p))
                    cooldown[sym] = ts + pd.Timedelta(hours=4)
                    del positions[sym]
                    closed = True

                if not closed:
                    if not p.tp1_done and l <= p.tp1:
                        equity += p.close_qty(p.init_qty * 0.40, p.tp1, "TP1_PART", ts)
                        p.tp1_done = True
                        p.tp1_bar = idx

                    if p.open() and not p.tp2_done and l <= p.tp2:
                        equity += p.close_qty(p.init_qty * 0.30, p.tp2, "TP2_PART", ts)
                        p.tp2_done = True

                    if p.open() and l <= p.tp3:
                        equity += p.close_qty(p.qty, p.tp3, "TP3", ts)
                        trades.append(make_trade(p))
                        cooldown[sym] = ts + pd.Timedelta(hours=4)
                        del positions[sym]
                        closed = True

        # 3) 평가자산 기록
        mtm = equity
        for sym, p in positions.items():
            df = data[sym]
            if ts in df.index:
                close = df.loc[ts, "close"]
                if p.side == "long":
                    mtm += p.qty * (close - p.entry)
                else:
                    mtm += p.qty * (p.entry - close)

        # regime 분류 — BTC 1h 기준
        btc_row = btc.loc[ts]
        regime = classify_regime(btc_row)

        curve.append({"time": ts, "equity": mtm, "regime": regime})

        # 4) 현재 봉 종가 기준 다음 봉 신호 생성
        # RANGE = 무거래 (시장 방향 애매)
        if regime == "RANGE":
            continue

        # 방향·strict·리스크를 regime 에 맞춰 설정
        if regime in ("STRONG_UP", "WEAK_UP"):
            allowed_side = "long"
            market = "up"
        else:  # STRONG_DOWN, WEAK_DOWN
            allowed_side = "short"
            market = "down"

        if regime in ("STRONG_UP", "STRONG_DOWN"):
            strict_local = False
            min_score_local = 9
            risk_local = RISK_PER_TRADE          # 0.5%
        else:  # WEAK_UP, WEAK_DOWN
            strict_local = True
            min_score_local = 12
            risk_local = RISK_PER_TRADE * 0.5    # 0.25%

        for sym, df in data.items():
            if EXCLUDE_BTC_FROM_TRADES and sym == "BTCUSDT":
                continue

            if sym in positions:
                continue

            if ts < cooldown[sym]:
                continue

            if ts not in df.index or next_ts not in df.index:
                continue

            row = df.loc[ts]

            side, score = raw_signal(row, market,
                                     strict_mode=strict_local,
                                     min_score=min_score_local)

            if side is None or side != allowed_side:
                continue

            signal_count += 1

            pending.append({
                "symbol": sym,
                "side": side,
                "score": score,
                "signal_row": row.copy(),
                "regime": regime,
                "risk_pct": risk_local,
            })

    # 마지막 강제청산
    final_ts = master_times[-1]

    for sym in list(positions.keys()):
        p = positions[sym]
        df = data[sym]

        if final_ts in df.index:
            raw_px = df.loc[final_ts, "close"]
        else:
            raw_px = p.entry

        equity += p.close_qty(p.qty, raw_px, "EOD", final_ts)
        trades.append(make_trade(p))
        del positions[sym]

    trades_df = pd.DataFrame(trades)
    curve_df = pd.DataFrame(curve)

    if trades_df.empty:
        summary = {
            "return": 0,
            "mdd": 0,
            "pf": np.nan,
            "win_rate": 0,
            "trades": 0,
            "long_trades": 0,
            "short_trades": 0,
            "max_losses": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "avg_rr": 0,
            "signal_count": signal_count,
            "rejected_stop": rejected_stop,
            "rejected_position_cap": rejected_position_cap,
            "rejected_cooldown": rejected_cooldown,
            "final_equity": START_EQUITY,
        }
        return summary, trades_df, curve_df

    wins = trades_df["pnl"] > 0

    gross_profit = trades_df.loc[trades_df["pnl"] > 0, "pnl"].sum()
    gross_loss = trades_df.loc[trades_df["pnl"] <= 0, "pnl"].sum()

    pf = gross_profit / abs(gross_loss) if gross_loss < 0 else np.inf

    losses = 0
    max_losses = 0

    for pnl in trades_df["pnl"]:
        if pnl <= 0:
            losses += 1
            max_losses = max(max_losses, losses)
        else:
            losses = 0

    avg_win = trades_df.loc[trades_df["pnl"] > 0, "pnl"].mean()
    avg_loss = trades_df.loc[trades_df["pnl"] <= 0, "pnl"].mean()

    side_counts = trades_df["side"].value_counts().to_dict()

    summary = {
        "return": equity / START_EQUITY - 1,
        "mdd": max_drawdown(curve_df["equity"].tolist()),
        "pf": pf,
        "win_rate": wins.mean() * 100,
        "trades": len(trades_df),
        "long_trades": side_counts.get("long", 0),
        "short_trades": side_counts.get("short", 0),
        "max_losses": max_losses,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "avg_rr": abs(avg_win / avg_loss) if avg_loss < 0 else np.inf,
        "signal_count": signal_count,
        "rejected_stop": rejected_stop,
        "rejected_position_cap": rejected_position_cap,
        "rejected_cooldown": rejected_cooldown,
        "final_equity": equity,
    }

    return summary, trades_df, curve_df


# =========================
# 실행
# =========================

t0 = time.time()

symbols = get_top_symbols(TOP_N) if AUTO_SYMBOLS else MANUAL_SYMBOLS
data = load_all_data(symbols)

summary, trades_df, curve_df = backtest(data)

summary_df = pd.DataFrame([summary])
display_df = summary_df.copy()
display_df["return"] *= 100
display_df["mdd"] *= 100

print("\n============================================================")
print("새 전략: BTC 1H 방향 + 15M 거래량 돌파 백테스트 요약")
print("============================================================\n")

pd.set_option("display.max_columns", 100)
pd.set_option("display.width", 260)

print(display_df.to_string(index=False))

if not trades_df.empty:
    print("\n============================================================")
    print("심볼별 성과")
    print("============================================================\n")

    sym_summary = trades_df.groupby("symbol").agg(
        trades=("pnl", "count"),
        pnl=("pnl", "sum"),
        win_rate=("pnl", lambda s: (s > 0).mean() * 100),
        avg_pnl=("pnl", "mean"),
        avg_score=("score", "mean"),
        avg_mfe=("mfe_pct", "mean"),
        avg_mae=("mae_pct", "mean"),
    ).reset_index().sort_values("pnl", ascending=False)

    print(sym_summary.to_string(index=False))

    print("\n============================================================")
    print("LONG / SHORT 성과")
    print("============================================================\n")

    side_summary = trades_df.groupby("side").agg(
        trades=("pnl", "count"),
        pnl=("pnl", "sum"),
        win_rate=("pnl", lambda s: (s > 0).mean() * 100),
        avg_pnl=("pnl", "mean"),
    ).reset_index().sort_values("pnl", ascending=False)

    print(side_summary.to_string(index=False))

    print("\n============================================================")
    print("Regime 분포 (curve 봉 기준)")
    print("============================================================\n")
    if "regime" in curve_df.columns:
        regime_counts = curve_df["regime"].value_counts()
        regime_pct = (regime_counts / len(curve_df) * 100).round(1)
        for r in ["STRONG_UP", "WEAK_UP", "RANGE", "WEAK_DOWN", "STRONG_DOWN"]:
            n = int(regime_counts.get(r, 0))
            p = float(regime_pct.get(r, 0.0))
            print(f"  {r:<12}  {n:>7,} 봉 ({p:>5.1f}%)")

    print("\n============================================================")
    print("Regime 별 성과")
    print("============================================================\n")

    regime_summary = trades_df.groupby("regime").agg(
        trades=("pnl", "count"),
        pnl=("pnl", "sum"),
        win_rate=("pnl", lambda s: (s > 0).mean() * 100),
        avg_pnl=("pnl", "mean"),
        long_pnl=("pnl", lambda s: s[trades_df.loc[s.index, "side"] == "long"].sum()),
        short_pnl=("pnl", lambda s: s[trades_df.loc[s.index, "side"] == "short"].sum()),
    ).reset_index().sort_values("pnl", ascending=False)

    print(regime_summary.to_string(index=False))

    print("\n============================================================")
    print("종료 사유별 성과")
    print("============================================================\n")

    exit_summary = trades_df.groupby("exit_reason").agg(
        trades=("pnl", "count"),
        pnl=("pnl", "sum"),
        avg_pnl=("pnl", "mean"),
        win_rate=("pnl", lambda s: (s > 0).mean() * 100),
    ).reset_index().sort_values("pnl", ascending=False)

    print(exit_summary.to_string(index=False))

    print("\n============================================================")
    print("월별 수익률")
    print("============================================================\n")

    curve_df["month"] = curve_df["time"].dt.to_period("M")
    monthly = curve_df.groupby("month")["equity"].agg(["first", "last"])
    monthly["return_pct"] = (monthly["last"] / monthly["first"] - 1) * 100

    print(monthly.reset_index().to_string(index=False))

    print("\n============================================================")
    print("최근 거래 30개")
    print("============================================================\n")

    recent_cols = [
        "symbol", "side", "entry_time", "exit_time", "exit_reason",
        "score", "risk_usdt", "pnl", "fees", "mae_pct", "mfe_pct"
    ]

    print(trades_df[recent_cols].tail(30).to_string(index=False))

print(f"\n실행 완료까지 {(time.time() - t0) * 1000:.1f}ms")
