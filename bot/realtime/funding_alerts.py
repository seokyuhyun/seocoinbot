"""펀딩비 spike 시그널.

!markPrice@arr@1s 스트림 (모든 심볼 매초) 받아서 모니터링 심볼만 필터.
펀딩비 절댓값이 임계 이상이고 이번 정산 기간에 아직 알림 안 보냈으면 텔레그램 전송.

펀딩비 의미:
- 양수 → 롱이 숏에 지급. 롱 과열. SHORT 진입 후보
- 음수 → 숏이 롱에 지급. 숏 과열. LONG 진입 후보
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Iterable

from .config import FUNDING_THRESHOLD
from .telegram import send as tg_send

log = logging.getLogger(__name__)


class FundingAlerter:
    """심볼별 펀딩 spike 감지·전송. 정산 기간(8h) 당 심볼 1회 알림."""

    def __init__(self, symbols: Iterable[str], threshold: float = FUNDING_THRESHOLD) -> None:
        self.symbols = set(symbols)
        self.threshold = threshold
        # symbol → next_funding_ts (이 정산 기간에 이미 알림 발송함)
        self._alerted_for_period: dict[str, int] = {}

    async def on_message(self, msg) -> None:
        """!markPrice@arr 메시지 처리.
        - 배열 (전체 심볼 1초 스트림) → list
        - 단일 (특정 심볼 1초 스트림) → dict
        """
        if isinstance(msg, list):
            for item in msg:
                await self._handle(item)
        elif isinstance(msg, dict):
            await self._handle(msg)

    async def _handle(self, item: dict) -> None:
        sym = item.get("s")
        if not sym or sym not in self.symbols:
            return
        try:
            funding = float(item.get("r", 0))
            mark = float(item.get("p", 0))
            next_ts = int(item.get("T", 0))
        except (TypeError, ValueError):
            return

        if abs(funding) < self.threshold:
            return

        # 이번 정산 기간에 이미 알림 보냈으면 스킵
        if self._alerted_for_period.get(sym) == next_ts:
            return

        await self._send_alert(sym, funding, mark, next_ts)
        self._alerted_for_period[sym] = next_ts

    async def _send_alert(self, sym: str, funding: float, mark: float, next_ts: int) -> None:
        side_desc = "SHORT 후보 (롱 과열)" if funding > 0 else "LONG 후보 (숏 과열)"
        emoji = "🔴" if funding > 0 else "🟢"

        next_dt = dt.datetime.fromtimestamp(next_ts / 1000, tz=dt.timezone.utc)
        now = dt.datetime.now(tz=dt.timezone.utc)
        delta = next_dt - now
        sec = max(0, int(delta.total_seconds()))
        hours, rem = divmod(sec, 3600)
        minutes = rem // 60

        text = (
            f"{emoji} *펀딩 spike — {side_desc}*\n"
            f"━━━━━━━━━━━━\n"
            f"심볼: `{sym}`\n"
            f"펀딩비: `{funding * 100:+.4f}%` "
            f"(임계 ±{self.threshold * 100:.3f}%)\n"
            f"마크가: `${mark:,.6g}`\n"
            f"다음 정산: `{hours}h {minutes}m` "
            f"({next_dt.strftime('%H:%M UTC')})\n"
        )

        log.info("[ALERT] %s funding=%+.4f%% mark=%g next=%s",
                 sym, funding * 100, mark, next_dt.strftime("%H:%M UTC"))
        await tg_send(text)
