"""Paper Trader — 알림 발생 즉시 가상 진입, mark price tick 마다 TP/SL 체크.

부분 청산 (TP1=40%, TP2=30%, TP3=30%) + SL 전량.
TIME_STOP: 진입 후 8h 경과 시 현재가에 강제 청산.

CSV 영구 저장 (재시작 후 통계 보존).

청산 알림 시 누적 통계 (승률 · PF · avg %) 함께 전송.
"""

from __future__ import annotations

import csv
import datetime as dt
import logging
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Literal, Optional

from .levels import TP_FRACTIONS, _fmt_price

log = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────
# Position
# ────────────────────────────────────────────────────────────

@dataclass
class PaperPosition:
    trade_id: str
    symbol: str
    side: Literal["long", "short"]
    leverage: int
    entry_time: dt.datetime
    entry_price: float
    tp1: float
    tp2: float
    tp3: float
    sl: float
    tp1_pct: float
    tp2_pct: float
    tp3_pct: float
    sl_pct: float
    funding_at_signal: float
    tier: str
    # 시그널 출처: "funding_spike" | "liquidation_cascade" | ...
    signal_type: str = "funding_spike"
    # 시그널별 raw context (cascade: 5분 청산금액·방향·가격변화 등)
    signal_meta: str = ""

    # state
    tps_hit: list[bool] = field(default_factory=lambda: [False, False, False])
    tps_time: list[Optional[dt.datetime]] = field(default_factory=lambda: [None, None, None])
    realized_pct: float = 0.0    # 누적 % (분할 청산 가중합)
    close_time: Optional[dt.datetime] = None
    close_reason: Optional[str] = None    # TP_ALL | SL | TIME_STOP

    def remaining_fraction(self) -> float:
        used = sum(TP_FRACTIONS[i] for i in range(3) if self.tps_hit[i])
        return max(0.0, 1.0 - used)

    def is_closed(self) -> bool:
        return self.close_time is not None

    def check(self, mark: float, ts: dt.datetime, time_stop_hours: int = 8) -> list[tuple]:
        """현재 mark 가격으로 레벨 체크. 트리거된 이벤트 list 반환.

        이벤트: ("tp1" | "tp2" | "tp3", pct) 또는 ("close", reason, realized_pct)
        """
        if self.is_closed():
            return []
        events = []

        # 시간 손절 먼저 체크
        elapsed_h = (ts - self.entry_time).total_seconds() / 3600
        if elapsed_h >= time_stop_hours:
            rem = self.remaining_fraction()
            if self.side == "long":
                unreal_pct = (mark - self.entry_price) / self.entry_price
            else:
                unreal_pct = (self.entry_price - mark) / self.entry_price
            self.realized_pct += unreal_pct * rem
            self.close_time = ts
            self.close_reason = "TIME_STOP"
            events.append(("close", "TIME_STOP", self.realized_pct))
            return events

        # SL 먼저 (보수)
        sl_hit = (
            (self.side == "long" and mark <= self.sl)
            or (self.side == "short" and mark >= self.sl)
        )
        if sl_hit:
            rem = self.remaining_fraction()
            self.realized_pct += -self.sl_pct * rem    # 손실은 음수
            self.close_time = ts
            self.close_reason = "SL"
            events.append(("close", "SL", self.realized_pct))
            return events

        # TPs (순서대로)
        tps = [self.tp1, self.tp2, self.tp3]
        tp_pcts = [self.tp1_pct, self.tp2_pct, self.tp3_pct]
        for k in range(3):
            if self.tps_hit[k]:
                continue
            tp = tps[k]
            triggered = (
                (self.side == "long" and mark >= tp)
                or (self.side == "short" and mark <= tp)
            )
            if triggered:
                self.tps_hit[k] = True
                self.tps_time[k] = ts
                self.realized_pct += tp_pcts[k] * TP_FRACTIONS[k]
                events.append((f"tp{k+1}", tp_pcts[k]))
                if k == 2:
                    self.close_time = ts
                    self.close_reason = "TP_ALL"
                    events.append(("close", "TP_ALL", self.realized_pct))
                    return events
            else:
                break  # 위 TP 안 닿으면 그 위는 더더욱 안 닿음 (LONG 기준 가격이 오르면서)

        return events


# ────────────────────────────────────────────────────────────
# Trader
# ────────────────────────────────────────────────────────────

class PaperTrader:
    """오픈 포지션·통계 관리. CSV 영구 저장."""

    CSV_FIELDS = [
        "trade_id", "symbol", "side", "leverage",
        "signal_type", "signal_meta",
        "entry_time", "entry_price",
        "tp1", "tp2", "tp3", "sl",
        "tp1_pct", "tp2_pct", "tp3_pct", "sl_pct",
        "funding_at_signal", "tier",
        "tp1_hit", "tp1_time",
        "tp2_hit", "tp2_time",
        "tp3_hit", "tp3_time",
        "close_time", "close_reason", "realized_pct",
    ]

    def __init__(self, csv_path: Path, max_concurrent: int = 5, time_stop_hours: int = 8):
        self.csv_path = csv_path
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_concurrent = max_concurrent
        self.time_stop_hours = time_stop_hours
        self.open_positions: dict[str, PaperPosition] = {}     # by symbol
        self.history: list[PaperPosition] = []                  # 이번 세션 완료된 거래
        self._stats_cache = None
        if not self.csv_path.exists():
            self._write_header()
        else:
            self._load_history()

    def _write_header(self):
        with self.csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=self.CSV_FIELDS)
            w.writeheader()

    def _load_history(self):
        """기존 CSV 에서 closed 거래 통계 누적."""
        try:
            with self.csv_path.open("r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if not row.get("close_time"):
                        continue
                    try:
                        pct = float(row.get("realized_pct", 0))
                    except (TypeError, ValueError):
                        continue
                    # 통계만 필요해서 dummy position 생성 — 메모리 비대화 방지
                    self.history.append(_HistoricalStub(
                        symbol=row["symbol"],
                        side=row["side"],
                        realized_pct=pct,
                        close_reason=row.get("close_reason", "?"),
                    ))
            log.info("paper_trades 기존 %d건 로드", len(self.history))
        except Exception as e:
            log.warning("CSV 로드 실패: %s", e)

    def open_position(
        self,
        symbol: str,
        side: Literal["long", "short"],
        entry_price: float,
        levels: dict,
        leverage: int,
        funding: float,
        ts: dt.datetime,
        signal_type: str = "funding_spike",
        signal_meta: str = "",
    ) -> Optional[PaperPosition]:
        """중복 진입 차단, 최대 동시 보유 제한."""
        if symbol in self.open_positions:
            return None
        if len(self.open_positions) >= self.max_concurrent:
            log.info("[paper] 최대 동시 보유 %d 도달 — %s 진입 스킵",
                     self.max_concurrent, symbol)
            return None
        pos = PaperPosition(
            trade_id=str(uuid.uuid4())[:8],
            symbol=symbol,
            side=side,
            leverage=leverage,
            entry_time=ts,
            entry_price=entry_price,
            tp1=levels["tp1"], tp2=levels["tp2"], tp3=levels["tp3"], sl=levels["sl"],
            tp1_pct=levels["tp1_pct"], tp2_pct=levels["tp2_pct"],
            tp3_pct=levels["tp3_pct"], sl_pct=levels["sl_pct"],
            funding_at_signal=funding,
            tier=levels["tier"],
            signal_type=signal_type,
            signal_meta=signal_meta,
        )
        self.open_positions[symbol] = pos
        log.info("[paper] OPEN %s %s @ %g (id=%s, %s)",
                 symbol, side.upper(), entry_price, pos.trade_id, signal_type)
        return pos

    def on_mark(self, symbol: str, mark: float, ts: dt.datetime) -> list[tuple]:
        """1 mark tick 처리. 트리거된 이벤트 list 반환 (호출자가 알림 전송)."""
        if symbol not in self.open_positions:
            return []
        pos = self.open_positions[symbol]
        events = pos.check(mark, ts, time_stop_hours=self.time_stop_hours)
        if pos.is_closed():
            self.history.append(pos)
            self._save(pos)
            del self.open_positions[symbol]
            self._stats_cache = None
        return events

    def stats(self) -> dict:
        """누적 통계."""
        if self._stats_cache is not None:
            return self._stats_cache
        n = len(self.history)
        if n == 0:
            return {
                "total": 0, "wins": 0, "losses": 0, "breakeven": 0,
                "win_rate": 0.0, "avg_pct": 0.0, "pf": float("nan"),
                "total_pct": 0.0,
                "tp_all": 0, "sl": 0, "time_stop": 0,
            }
        wins, losses, breakeven = 0, 0, 0
        win_sum, loss_sum, total = 0.0, 0.0, 0.0
        tp_all, sl, time_stop = 0, 0, 0
        for p in self.history:
            total += p.realized_pct
            if p.realized_pct > 0.0005:
                wins += 1; win_sum += p.realized_pct
            elif p.realized_pct < -0.0005:
                losses += 1; loss_sum += p.realized_pct
            else:
                breakeven += 1
            cr = getattr(p, "close_reason", "?") or "?"
            if cr == "TP_ALL":
                tp_all += 1
            elif cr == "SL":
                sl += 1
            elif cr == "TIME_STOP":
                time_stop += 1
        pf = (win_sum / abs(loss_sum)) if loss_sum < 0 else float("inf")
        out = {
            "total": n, "wins": wins, "losses": losses, "breakeven": breakeven,
            "win_rate": wins / n * 100,
            "avg_pct": total / n * 100,
            "pf": pf,
            "total_pct": total * 100,
            "tp_all": tp_all, "sl": sl, "time_stop": time_stop,
        }
        self._stats_cache = out
        return out

    def _save(self, pos: PaperPosition):
        try:
            with self.csv_path.open("a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=self.CSV_FIELDS)
                w.writerow({
                    "trade_id": pos.trade_id,
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "leverage": pos.leverage,
                    "signal_type": pos.signal_type,
                    "signal_meta": pos.signal_meta,
                    "entry_time": pos.entry_time.isoformat(),
                    "entry_price": pos.entry_price,
                    "tp1": pos.tp1, "tp2": pos.tp2, "tp3": pos.tp3, "sl": pos.sl,
                    "tp1_pct": pos.tp1_pct, "tp2_pct": pos.tp2_pct,
                    "tp3_pct": pos.tp3_pct, "sl_pct": pos.sl_pct,
                    "funding_at_signal": pos.funding_at_signal,
                    "tier": pos.tier,
                    "tp1_hit": pos.tps_hit[0],
                    "tp1_time": pos.tps_time[0].isoformat() if pos.tps_time[0] else "",
                    "tp2_hit": pos.tps_hit[1],
                    "tp2_time": pos.tps_time[1].isoformat() if pos.tps_time[1] else "",
                    "tp3_hit": pos.tps_hit[2],
                    "tp3_time": pos.tps_time[2].isoformat() if pos.tps_time[2] else "",
                    "close_time": pos.close_time.isoformat() if pos.close_time else "",
                    "close_reason": pos.close_reason or "",
                    "realized_pct": pos.realized_pct,
                })
        except Exception as e:
            log.error("CSV 저장 실패: %s", e)


@dataclass
class _HistoricalStub:
    """CSV 로드 시 stats 계산용 경량 객체."""
    symbol: str
    side: str
    realized_pct: float
    close_reason: str


# ────────────────────────────────────────────────────────────
# Telegram 알림 포맷 (청산 시)
# ────────────────────────────────────────────────────────────

_REASON_KR_BINANCE = {
    "TP_ALL":      ("🟢🟢🟢", "모든 익절 성공",       "🎉 1·2·3차 다 익절"),
    "SL":          ("🔴",     "손절",                  "😔 손절가 도달"),
    "BE_STOP":     ("⚪",     "본절 청산",             "😐 1차 익절 후 본전 청산 (가격 되돌아옴)"),
    "TIME_STOP":   ("⏱",     "시간 만료",             "😐 8시간 안에 안 끝남 — 현재가 정리"),
    "LIQUIDATED":  ("💥",     "강제청산",              "💀 청산가 도달 — 마진 전손"),
    "FORCED_FINAL": ("ℹ️",     "테스트 종료 청산",      "🔚 봇 테스트 끝 — 강제 정리"),
}


def format_close_alert(pos: PaperPosition, reason: str, stats: dict) -> str:
    """청산 알림 — 초보자 친화."""
    emoji, reason_kr, result_kr = _REASON_KR_BINANCE.get(
        reason, ("ℹ️", reason, reason)
    )
    side_text = "매수(LONG)" if pos.side == "long" else "매도(SHORT)"

    duration = pos.close_time - pos.entry_time
    hours, rem = divmod(int(duration.total_seconds()), 3600)
    minutes = rem // 60

    pct = pos.realized_pct * 100
    pct_label = "수익" if pct > 0 else ("손실" if pct < 0 else "본전")
    pf_str = "inf" if stats["pf"] == float("inf") else f"{stats['pf']:.2f}"
    tp_breakdown = (
        f"1차 {'✓' if pos.tps_hit[0] else '✗'} "
        f"2차 {'✓' if pos.tps_hit[1] else '✗'} "
        f"3차 {'✓' if pos.tps_hit[2] else '✗'}"
    )

    return (
        f"{emoji} *가상매매 청산 — {reason_kr}* (Binance 선물)\n"
        f"#{pos.symbol} {side_text} {pos.leverage}배 청산\n"
        f"━━━━━━━━━━━━\n"
        f"💵 진입가: `{_fmt_price(pos.entry_price)}`\n"
        f"{result_kr}\n"
        f"💰 결과: `{pct:+.2f}%` {pct_label}\n"
        f"⏰ 보유 시간: `{hours}h {minutes}m`\n"
        f"🎯 익절 도달: {tp_breakdown}\n"
        f"━━ 지금까지 통계 ━━\n"
        f"총 거래: `{stats['total']}`건 "
        f"(승 `{stats['wins']}` · 패 `{stats['losses']}` · 본전 `{stats['breakeven']}`)\n"
        f"승률: `{stats['win_rate']:.1f}%`\n"
        f"평균 수익: `{stats['avg_pct']:+.3f}%` / 거래\n"
        f"손익비 (PF): `{pf_str}`  _(1 이상이면 양수 edge)_\n"
        f"누적 수익률: `{stats['total_pct']:+.2f}%`\n"
        f"청산 사유: 완전익절 `{stats['tp_all']}` · "
        f"손절 `{stats['sl']}` · 시간만료 `{stats['time_stop']}`\n"
    )
