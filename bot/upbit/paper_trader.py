"""Upbit 현물 Paper Trader.

Binance 봇 paper_trader 와 구조 유사하지만:
- 현물 = LONG only
- 레버리지 = 1 고정 (청산 없음)
- TIME_STOP 짧음 (Upbit 빠른 거래라 2시간)
"""

from __future__ import annotations

import csv
import datetime as dt
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .levels import TP_FRACTIONS, _fmt_krw, _fmt_vol_krw

log = logging.getLogger(__name__)


@dataclass
class PaperPosition:
    trade_id: str
    market: str            # KRW-BTC 등
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
    tier: str
    signal_type: str = "volume_spike"
    signal_meta: str = ""

    tps_hit: list[bool] = field(default_factory=lambda: [False, False, False])
    tps_time: list[Optional[dt.datetime]] = field(default_factory=lambda: [None, None, None])
    realized_pct: float = 0.0
    close_time: Optional[dt.datetime] = None
    close_reason: Optional[str] = None    # TP_ALL | SL | TIME_STOP

    def remaining_fraction(self) -> float:
        used = sum(TP_FRACTIONS[i] for i in range(3) if self.tps_hit[i])
        return max(0.0, 1.0 - used)

    def is_closed(self) -> bool:
        return self.close_time is not None

    def check(self, mark: float, ts: dt.datetime, time_stop_hours: int = 2) -> list[tuple]:
        if self.is_closed():
            return []
        events = []

        # 시간 손절
        elapsed_h = (ts - self.entry_time).total_seconds() / 3600
        if elapsed_h >= time_stop_hours:
            rem = self.remaining_fraction()
            unreal = (mark - self.entry_price) / self.entry_price
            self.realized_pct += unreal * rem
            self.close_time = ts
            self.close_reason = "TIME_STOP"
            events.append(("close", "TIME_STOP", self.realized_pct))
            return events

        # SL (현물 = LONG. 하락 시 손절)
        if mark <= self.sl:
            rem = self.remaining_fraction()
            self.realized_pct += -self.sl_pct * rem
            self.close_time = ts
            self.close_reason = "SL"
            events.append(("close", "SL", self.realized_pct))
            return events

        # TP1/2/3
        tps = [self.tp1, self.tp2, self.tp3]
        tp_pcts = [self.tp1_pct, self.tp2_pct, self.tp3_pct]
        for k in range(3):
            if self.tps_hit[k]:
                continue
            if mark >= tps[k]:
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
                break

        return events


class PaperTrader:
    CSV_FIELDS = [
        "trade_id", "market", "signal_type", "signal_meta",
        "entry_time", "entry_price",
        "tp1", "tp2", "tp3", "sl",
        "tp1_pct", "tp2_pct", "tp3_pct", "sl_pct", "tier",
        "tp1_hit", "tp1_time",
        "tp2_hit", "tp2_time",
        "tp3_hit", "tp3_time",
        "close_time", "close_reason", "realized_pct",
    ]

    def __init__(self, csv_path: Path, max_concurrent: int = 5, time_stop_hours: int = 2):
        self.csv_path = csv_path
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_concurrent = max_concurrent
        self.time_stop_hours = time_stop_hours
        self.open_positions: dict[str, PaperPosition] = {}
        self.history: list = []
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
                    self.history.append(_HistStub(
                        market=row.get("market", "?"),
                        realized_pct=pct,
                        close_reason=row.get("close_reason", "?"),
                    ))
            log.info("upbit paper_trades 기존 %d건 로드", len(self.history))
        except Exception as e:
            log.warning("CSV 로드 실패: %s", e)

    def open_position(
        self, market: str, entry_price: float, levels: dict,
        ts: dt.datetime, signal_type: str = "volume_spike",
        signal_meta: str = "",
    ) -> Optional[PaperPosition]:
        if market in self.open_positions:
            return None
        if len(self.open_positions) >= self.max_concurrent:
            log.info("[paper] 동시 보유 %d 도달 — %s 스킵",
                     self.max_concurrent, market)
            return None
        pos = PaperPosition(
            trade_id=str(uuid.uuid4())[:8],
            market=market, entry_time=ts, entry_price=entry_price,
            tp1=levels["tp1"], tp2=levels["tp2"], tp3=levels["tp3"], sl=levels["sl"],
            tp1_pct=levels["tp1_pct"], tp2_pct=levels["tp2_pct"],
            tp3_pct=levels["tp3_pct"], sl_pct=levels["sl_pct"],
            tier=levels["tier"],
            signal_type=signal_type, signal_meta=signal_meta,
        )
        self.open_positions[market] = pos
        log.info("[paper] OPEN %s LONG @ %g (id=%s, %s)",
                 market, entry_price, pos.trade_id, signal_type)
        return pos

    def on_price(self, market: str, mark: float, ts: dt.datetime) -> list[tuple]:
        if market not in self.open_positions:
            return []
        pos = self.open_positions[market]
        events = pos.check(mark, ts, time_stop_hours=self.time_stop_hours)
        if pos.is_closed():
            self.history.append(pos)
            self._save(pos)
            del self.open_positions[market]
            self._stats_cache = None
        return events

    def stats(self) -> dict:
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
        wins, losses, be = 0, 0, 0
        win_sum, loss_sum, total = 0.0, 0.0, 0.0
        tp_all, sl_n, time_stop = 0, 0, 0
        for p in self.history:
            total += p.realized_pct
            if p.realized_pct > 0.0005:
                wins += 1; win_sum += p.realized_pct
            elif p.realized_pct < -0.0005:
                losses += 1; loss_sum += p.realized_pct
            else:
                be += 1
            cr = getattr(p, "close_reason", "?") or "?"
            if cr == "TP_ALL": tp_all += 1
            elif cr == "SL": sl_n += 1
            elif cr == "TIME_STOP": time_stop += 1
        pf = (win_sum / abs(loss_sum)) if loss_sum < 0 else float("inf")
        out = {
            "total": n, "wins": wins, "losses": losses, "breakeven": be,
            "win_rate": wins / n * 100,
            "avg_pct": total / n * 100,
            "pf": pf,
            "total_pct": total * 100,
            "tp_all": tp_all, "sl": sl_n, "time_stop": time_stop,
        }
        self._stats_cache = out
        return out

    def _save(self, pos: PaperPosition):
        try:
            with self.csv_path.open("a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=self.CSV_FIELDS)
                w.writerow({
                    "trade_id": pos.trade_id, "market": pos.market,
                    "signal_type": pos.signal_type, "signal_meta": pos.signal_meta,
                    "entry_time": pos.entry_time.isoformat(),
                    "entry_price": pos.entry_price,
                    "tp1": pos.tp1, "tp2": pos.tp2, "tp3": pos.tp3, "sl": pos.sl,
                    "tp1_pct": pos.tp1_pct, "tp2_pct": pos.tp2_pct,
                    "tp3_pct": pos.tp3_pct, "sl_pct": pos.sl_pct,
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
class _HistStub:
    market: str
    realized_pct: float
    close_reason: str


def format_close_alert(pos: PaperPosition, reason: str, stats: dict) -> str:
    emoji_map = {"TP_ALL": "🟢🟢🟢", "SL": "🔴", "TIME_STOP": "⏱"}
    emoji = emoji_map.get(reason, "ℹ️")
    sym_short = pos.market.replace("KRW-", "")
    duration = pos.close_time - pos.entry_time
    h, rem = divmod(int(duration.total_seconds()), 3600)
    m = rem // 60
    pct = pos.realized_pct * 100
    pct_emoji = "📈" if pct > 0 else ("📉" if pct < 0 else "➡")
    pf_str = "inf" if stats["pf"] == float("inf") else f"{stats['pf']:.2f}"
    return (
        f"{emoji} *PAPER 청산 — {reason}* (Upbit)\n"
        f"#{sym_short}/KRW LONG\n"
        f"━━━━━━━━━━━━\n"
        f"진입: `{_fmt_krw(pos.entry_price)}`\n"
        f"청산 사유: `{reason}`\n"
        f"실현: {pct_emoji} `{pct:+.2f}%`\n"
        f"보유: `{h}h {m}m`\n"
        f"TP: `{int(pos.tps_hit[0])}/{int(pos.tps_hit[1])}/{int(pos.tps_hit[2])}`\n"
        f"━━ 누적 통계 ━━\n"
        f"거래: `{stats['total']}` (승 `{stats['wins']}` · 패 `{stats['losses']}`)\n"
        f"승률: `{stats['win_rate']:.1f}%`\n"
        f"평균: `{stats['avg_pct']:+.3f}%/거래`\n"
        f"PF: `{pf_str}`\n"
        f"누적 수익: `{stats['total_pct']:+.2f}%`\n"
        f"TP_ALL/SL/TIME: `{stats['tp_all']}/{stats['sl']}/{stats['time_stop']}`\n"
    )
