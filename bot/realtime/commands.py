"""Telegram 명령어 폴링·라우팅.

지원 명령 (모두 2 suffix — 다른 봇과 명령 분리):
- /help2, /start2   도움말
- /status2          현재 상태 (가동시간, 오픈 포지션)
- /summary2         누적 통계 (승률, PF, 시그널 타입별)
- /pause2           새 시그널 발사 일시정지 (기존 포지션은 계속 모니터링)
- /resume2          시그널 재개
- /stop2            봇 종료 (graceful)

/start (텔레그램 'Start' 버튼이 자동 전송) 만 호환을 위해 그대로 살림 — help 표시.

인증: TELEGRAM_CHAT_IDS 에 등록된 chat 만 응답.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from .config import TELEGRAM_CHAT_IDS
from .paper_trader import PaperTrader
from .telegram import get_updates, send_to

log = logging.getLogger(__name__)


class BotState:
    """공유 상태 — pause/shutdown 플래그."""

    def __init__(self) -> None:
        self.paused: bool = False
        self.shutdown: asyncio.Event = asyncio.Event()
        self.start_time: dt.datetime = dt.datetime.now(dt.timezone.utc)


class CommandRouter:
    def __init__(self, state: BotState, paper: PaperTrader, handler=None) -> None:
        self.state = state
        self.paper = paper
        self.handler = handler   # /now2 등 진단용 (지연 주입)

    async def handle(self, cmd: str, args: str, sender_chat_id: str) -> str:
        cmd = cmd.lower()
        # /start (텔레그램 Start 버튼) + /start2 + /help2 모두 help 표시
        if cmd in ("/start", "/start2", "/help2"):
            return self._help()
        if cmd == "/status2":
            return self._status()
        if cmd == "/summary2":
            return self._summary()
        if cmd == "/now2":
            return self._now()
        if cmd == "/pause2":
            self.state.paused = True
            return (
                "⏸ *봇 일시정지*\n"
                "새 시그널 발사 안 함. 기존 paper 포지션은 계속 모니터링됨.\n"
                "재개: `/resume2`"
            )
        if cmd == "/resume2":
            self.state.paused = False
            return "▶ *봇 재개*\n새 시그널 다시 받습니다."
        if cmd == "/stop2":
            self.state.shutdown.set()
            return "🛑 *봇 종료 중...*\n잠시 후 작별 메시지 옵니다."
        return f"알 수 없는 명령: `{cmd}`\n도움말: `/help2`"

    def _help(self) -> str:
        return (
            "*🤖 seocoinbot 명령어*\n"
            "━━━━━━━━━━━━\n"
            "`/status2` — 현재 상태 (가동시간·오픈 포지션)\n"
            "`/summary2` — 누적 통계 (승률·PF)\n"
            "`/now2` — 지금 모니터 중인 심볼들의 펀딩비 top 15\n"
            "`/pause2` — 새 시그널 일시정지\n"
            "`/resume2` — 시그널 재개\n"
            "`/stop2` — 봇 종료\n"
            "`/help2` — 이 도움말\n"
        )

    def _now(self) -> str:
        """진단용 — 지금 본 펀딩비 top 15. 임계 통과 가까운 코인 확인."""
        if self.handler is None or not self.handler.last_funding:
            return (
                "_(아직 펀딩비 데이터 수집 안 됨 — 봇 시작 직후?)_\n"
                "잠시 후 다시 시도해주세요."
            )
        items = sorted(
            self.handler.last_funding.items(),
            key=lambda x: -abs(x[1]),
        )[:15]
        from .config import FUNDING_THRESHOLD
        lines = [
            "*📡 모니터 심볼 펀딩비 top 15* (절댓값 큰 순)",
            f"임계: `±{FUNDING_THRESHOLD * 100:.3f}%`",
            "━━━━━━━━━━━━",
        ]
        for sym, f in items:
            mark = " ⚡" if abs(f) >= FUNDING_THRESHOLD else ""
            lines.append(f"`{sym:<14}` `{f*100:>+7.4f}%`{mark}")
        n_above = sum(1 for _, f in self.handler.last_funding.items()
                      if abs(f) >= FUNDING_THRESHOLD)
        lines.append("━━━━━━━━━━━━")
        lines.append(
            f"전체 `{len(self.handler.last_funding)}` 심볼 중 "
            f"`{n_above}` 개가 임계 이상"
        )
        if n_above == 0:
            lines.append("_⚠ 임계 통과 코인 없음 → 시간 더 기다리거나 임계 낮춰야_")
        return "\n".join(lines)

    def _status(self) -> str:
        n_open = len(self.paper.open_positions)
        uptime = dt.datetime.now(dt.timezone.utc) - self.state.start_time
        sec = max(0, int(uptime.total_seconds()))
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        paused_str = "⏸ PAUSED" if self.state.paused else "▶ RUNNING"
        lines = [
            f"*🤖 상태: {paused_str}*",
            "━━━━━━━━━━━━",
            f"가동시간: `{h}h {m}m {s}s`",
            f"오픈 paper 포지션: `{n_open}`",
        ]
        if n_open > 0:
            lines.append("")
            now = dt.datetime.now(dt.timezone.utc)
            for sym, pos in self.paper.open_positions.items():
                age = int((now - pos.entry_time).total_seconds() / 60)
                tps_done = sum(pos.tps_hit)
                lines.append(
                    f"  • #{sym} {pos.side.upper()} {pos.leverage}x "
                    f"@ `{pos.entry_price:g}` "
                    f"({age}m, TP `{tps_done}/3`, {pos.signal_type})"
                )
        return "\n".join(lines)

    def _summary(self) -> str:
        s = self.paper.stats()
        if s["total"] == 0:
            return (
                "*📊 누적 통계*\n"
                "━━━━━━━━━━━━\n"
                "_(아직 청산된 거래 없음)_\n"
                f"오픈: `{len(self.paper.open_positions)}` 포지션\n"
            )
        pf = "inf" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
        return (
            "*📊 누적 통계*\n"
            "━━━━━━━━━━━━\n"
            f"거래: `{s['total']}` "
            f"(승 `{s['wins']}` · 패 `{s['losses']}` · BE `{s['breakeven']}`)\n"
            f"승률: `{s['win_rate']:.1f}%`\n"
            f"평균: `{s['avg_pct']:+.3f}%/거래`\n"
            f"PF: `{pf}`\n"
            f"누적 수익: `{s['total_pct']:+.2f}%`\n"
            f"종료 사유: TP_ALL `{s['tp_all']}` · "
            f"SL `{s['sl']}` · TIME `{s['time_stop']}`\n"
        )


async def poll_commands(router: CommandRouter, state: BotState) -> None:
    """장기 폴링 루프. shutdown event 또는 cancel 로 종료."""
    # 시작 시 백로그 드레인 (이전 /stop 잔재 무시)
    try:
        initial = await get_updates(offset=0, timeout=0)
        offset = max((u.get("update_id", 0) for u in initial), default=-1) + 1
    except Exception:
        offset = 0
    log.info("명령 폴링 시작 (offset=%d)", offset)

    while not state.shutdown.is_set():
        try:
            updates = await get_updates(offset, timeout=25)
            for update in updates:
                uid = update.get("update_id", 0)
                if uid >= offset:
                    offset = uid + 1
                msg = update.get("message")
                if not isinstance(msg, dict):
                    continue
                text = (msg.get("text") or "").strip()
                if not text.startswith("/"):
                    continue
                chat_id = str((msg.get("chat") or {}).get("id", ""))
                if chat_id not in TELEGRAM_CHAT_IDS:
                    log.warning("비인가 chat_id %s 명령 무시: %s",
                                chat_id, text[:40])
                    continue

                cmd_part, _, args = text.partition(" ")
                if "@" in cmd_part:
                    cmd_part = cmd_part.split("@")[0]

                log.info("명령 %s from %s", cmd_part, chat_id)
                resp = await router.handle(cmd_part, args, chat_id)
                if resp:
                    await send_to(chat_id, resp)
        except asyncio.CancelledError:
            log.info("명령 폴링 취소됨")
            raise
        except Exception as e:
            log.error("poll_commands 에러: %s", e)
            await asyncio.sleep(3)
