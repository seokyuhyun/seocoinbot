"""Upbit 봇 설정 — .env 로드."""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass


# ── Telegram (Upbit 전용 — Binance 봇과 다른 토큰) ────────
TELEGRAM_BOT_TOKEN = os.getenv("UPBIT_TELEGRAM_BOT_TOKEN", "").strip()
_chat_raw = os.getenv("UPBIT_TELEGRAM_CHAT_IDS", "").strip()
TELEGRAM_CHAT_IDS: list[str] = [c.strip() for c in _chat_raw.split(",") if c.strip()]


# ── 모니터 범위 ──────────────────────────────────────────
TOP_N_SYMBOLS = int(os.getenv("UPBIT_TOP_N_SYMBOLS", "30"))
SYMBOL_REFRESH_MINUTES = int(os.getenv("UPBIT_SYMBOL_REFRESH_MINUTES", "60"))

# REST 폴링 주기 (초) — 1분 캔들 가져오는 간격
CANDLE_POLL_INTERVAL_SEC = int(os.getenv("UPBIT_CANDLE_POLL_SEC", "30"))


# ── 거래량 spike 임계 ─────────────────────────────────────
# 현재 1분 거래량 / 직전 N분 평균 ≥ 이 배수 → spike
VOLUME_SPIKE_MULT = float(os.getenv("UPBIT_VOLUME_SPIKE_MULT", "3.0"))
# 평균 계산용 직전 분 수
VOLUME_AVG_MINUTES = int(os.getenv("UPBIT_VOLUME_AVG_MINUTES", "20"))
# 너무 작은 거래대금 마켓은 제외 (1분 평균 KRW 거래대금 최소)
MIN_AVG_VOL_KRW = float(os.getenv("UPBIT_MIN_AVG_VOL_KRW", "10000000"))   # 1천만원
# 같은 마켓 재발사 쿨다운 (분)
COOLDOWN_MINUTES = int(os.getenv("UPBIT_COOLDOWN_MINUTES", "30"))


# ── Paper trade 설정 ──────────────────────────────────────
# 현물 = 레버리지 1 고정
LEVERAGE = 1
MAX_CONCURRENT = int(os.getenv("UPBIT_MAX_CONCURRENT", "5"))
# 시간 손절 (시간) — Upbit 빠른 거래라 2시간이면 충분
TIME_STOP_HOURS = int(os.getenv("UPBIT_TIME_STOP_HOURS", "2"))


# ── Upbit 엔드포인트 ──────────────────────────────────────
UPBIT_REST = "https://api.upbit.com/v1"
UPBIT_WS = "wss://api.upbit.com/websocket/v1"


# ── 로깅 / 저장 ───────────────────────────────────────────
LOG_LEVEL = os.getenv("UPBIT_LOG_LEVEL", "INFO").upper()

_paper_dir = Path(__file__).resolve().parent.parent.parent / "data" / "upbit_realtime"
PAPER_TRADES_CSV = _paper_dir / "paper_trades.csv"

# 일일 요약 전송 주기 (시간). 0 = 비활성.
DAILY_SUMMARY_HOURS = int(os.getenv("UPBIT_DAILY_SUMMARY_HOURS", "24"))
