"""실행 설정 — .env 에서 로드. 누락 값은 main 에서 경고."""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    # python-dotenv 미설치 — os.environ 만 사용
    pass


# ── Telegram ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# 복수 chat_id 지원: 콤마로 구분
_chat_raw = os.getenv("TELEGRAM_CHAT_IDS", "").strip()
if not _chat_raw:
    # 단수형 폴백 (기존 .env 호환)
    _chat_raw = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_CHAT_IDS: list[str] = [c.strip() for c in _chat_raw.split(",") if c.strip()]


# ── 모니터링 범위 ─────────────────────────────────────────
TOP_N_SYMBOLS = int(os.getenv("REALTIME_TOP_N_SYMBOLS", "30"))


# ── 펀딩 시그널 설정 ──────────────────────────────────────
# 임계: |funding rate| 이 이 값 이상이면 알림
FUNDING_THRESHOLD = float(os.getenv("REALTIME_FUNDING_THRESHOLD", "0.0005"))   # 0.05%

# 알림 후 중복 방지 — 다음 정산까지는 같은 심볼 재알림 안 함 (코드에서 자동)


# ── 시그널 / Paper trade 설정 ──────────────────────────────
LEVERAGE = int(os.getenv("REALTIME_LEVERAGE", "5"))   # 신호에 표기 + paper 통계용
MAX_CONCURRENT_PAPER = int(os.getenv("REALTIME_MAX_CONCURRENT", "5"))
PAPER_TIME_STOP_HOURS = int(os.getenv("REALTIME_TIME_STOP_HOURS", "8"))

# Paper trade 영구 저장 경로
_paper_dir = Path(__file__).resolve().parent.parent.parent / "data" / "realtime"
PAPER_TRADES_CSV = _paper_dir / "paper_trades.csv"


# ── 바이낸스 엔드포인트 ───────────────────────────────────
BINANCE_FAPI = "https://fapi.binance.com/fapi/v1"
BINANCE_WS = "wss://fstream.binance.com/ws"


# ── 로깅 ──────────────────────────────────────────────────
LOG_LEVEL = os.getenv("REALTIME_LOG_LEVEL", "INFO").upper()
