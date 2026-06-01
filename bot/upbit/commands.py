"""Upbit 봇 Telegram 명령어 (Binance 봇과 토큰 다름. 이름 충돌 X).

명령: /help /status /summary /now /pause /resume /stop /start
(Binance 봇은 /help2 등 2 suffix — 다른 봇이라 충돌 안 함)
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
    def __init__(self) -> None:
        self.paused: bool = False
        self.shutdown: asyncio.Event = asyncio.Event()
        self.start_time: dt.datetime = dt.datetime.now(dt.timezone.utc)


class CommandRouter:
    def __init__(self, state: BotState, paper: PaperTrader, detector=None) -> None:
        self.state = state
        self.paper = paper
        self.detector = detector

    async def handle(self, cmd: str, args: str, sender_chat_id: str) -> str:
        cmd = cmd.lower()
        if cmd in ("/start", "/help"):
            return self._help()
        if cmd == "/status":
            return self._status()
        if cmd == "/summary":
            return self._summary()
        if cmd == "/now":
            return self._now()
        if cmd == "/pause":
            self.state.paused = True
            return "⏸ *Upbit 봇 일시정지*\n새 시그널 안 받음. 기존 포지션 모니터링 계속."
        if cmd == "/resume":
            self.state.paused = False
            return "▶ *Upbit 봇 재개*"
        if cmd == "/stop":
            self.state.shutdown.set()
            return "🛑 *Upbit 봇 종료 중...*"
        return f"알 수 없는 명령: `{cmd}`\n도움말: `/help`"

    def _help(self) -> str:
        return (
            "*🤖 Upbit 봇 명령어*\n"
            "━━━━━━━━━━━━\n"
            "`/status` — 가동시간·오픈 포지션\n"
            "`/summary` — 누적 통계 (승률·PF)\n"
            "`/now` — 모니터 중인 KRW 마켓 정보\n"
            "`/pause` — 시그널 일시정지\n"
            "`/resume` — 재개\n"
            "`/stop` — 종료\n"
            "`/help` — 도움말\n"
        )

    def _status(self) -> str:
        n_open = len(self.paper.open_positions)
        uptime = dt.datetime.now(dt.timezone.utc) - self.state.start_time
        sec = max(0, int(uptime.total_seconds()))
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        paused_str = "⏸ PAUSED" if self.state.paused else "▶ RUNNING"
        lines = [
            f"*🤖 Upbit 봇 상태: {paused_str}*",
            "━━━━━━━━━━━━",
            f"가동시간: `{h}h {m}m {s}s`",
            f"오픈 paper 포지션: `{n_open}`",
        ]
        if n_open > 0:
            lines.append("")
            now = dt.datetime.now(dt.timezone.utc)
            for mk, pos in self.paper.open_positions.items():
                age = int((now - pos.entry_time).total_seconds() / 60)
                tps_done = sum(pos.tps_hit)
                lines.append(
                    f"  • #{mk} @ `{pos.entry_price:g}` "
                    f"({age}m, TP `{tps_done}/3`)"
                )
        return "\n".join(lines)

    def _summary(self) -> str:
        s = self.paper.stats()
        if s["total"] == 0:
            return (
                "*📊 Upbit 누적 통계*\n"
                "━━━━━━━━━━━━\n"
                "_(아직 청산된 거래 없음)_\n"
                f"오픈: `{len(self.paper.open_positions)}` 포지션\n"
            )
        pf = "inf" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
        return (
            "*📊 Upbit 누적 통계*\n"
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

    def _now(self) -> str:
        if self.detector is None:
            return "_(detector 정보 없음)_"
        return (
            f"*📡 Upbit 모니터*\n"
            f"━━━━━━━━━━━━\n"
            f"마켓 수: `{len(self.detector.markets)}`\n"
            f"마켓 list: `{', '.join(sorted(self.detector.markets)[:20])}`\n"
            f"...\n"
            f"임계: 1분 거래량 ≥ 20분 평균 × `{self.detector.spike_mult:.1f}`\n"
            f"최소 평균 거래대금: `{self.detector.min_avg_vol_krw/1e6:.0f}M원`\n"
        )


async def poll_commands(router: CommandRouter, state: BotState) -> None:
    try:
        initial = await get_updates(offset=0, timeout=0)
        offset = max((u.get("update_id", 0) for u in initial), default=-1) + 1
    except Exception:
        offset = 0
    log.info("Upbit 명령 폴링 시작 (offset=%d)", offset)
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
                    log.warning("비인가 chat %s: %s", chat_id, text[:40])
                    continue
                cmd_part, _, args = text.partition(" ")
                if "@" in cmd_part:
                    cmd_part = cmd_part.split("@")[0]
                log.info("Upbit 명령 %s from %s", cmd_part, chat_id)
                resp = await router.handle(cmd_part, args, chat_id)
                if resp:
                    await send_to(chat_id, resp)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("poll_commands 에러: %s", e)
            await asyncio.sleep(3)
