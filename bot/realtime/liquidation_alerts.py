"""청산 캐스케이드 감지 + 시그널.

`!forceOrder@arr` 스트림 구독: 모든 강제청산 실시간 push.
심볼별 5분 롤링 윈도우로 누적:
  - 총 청산 USD
  - 롱 청산 USD / 숏 청산 USD
  - 5분 전 마크가 (price_change 계산용)

발사 조건 (top-N 심볼만):
  1) 5분 총청산 ≥ MIN_TOTAL_USD ($500k 기본)
  2) 한쪽이 80% 이상 차지 (쏠림 명확)
  3) 가격 변화 절댓값 ≥ 1.5%
  4) 같은 심볼 30분 쿨다운

발사 방향:
  - 롱 청산 다수 → 가격 하락 → 반발 LONG
  - 숏 청산 다수 → 가격 상승 → 반발 SHORT
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import deque
from dataclasses import dataclass
from typing import Iterable

log = logging.getLogger(__name__)


# ── 감지 임계값 ─────────────────────────────────────────────
WINDOW_MINUTES = 5
MIN_TOTAL_LIQ_USD = 500_000.0          # $500k
MIN_LOPSIDED_RATIO = 0.80              # 한쪽 80% 이상
MIN_PRICE_CHANGE_PCT = 0.015           # ±1.5%
COOLDOWN_MINUTES = 30                  # 같은 심볼 재발사 금지 기간


@dataclass
class _LiqEvent:
    ts: dt.datetime
    side: str              # "long"=롱청산(SELL), "short"=숏청산(BUY)
    usd: float


class CascadeDetector:
    """심볼별 청산 + 가격 롤링 윈도우 관리. 캐스케이드 발생 시 신호 반환."""

    def __init__(
        self,
        symbols: Iterable[str],
        window_minutes: int = WINDOW_MINUTES,
        min_total_usd: float = MIN_TOTAL_LIQ_USD,
        min_lopsided: float = MIN_LOPSIDED_RATIO,
        min_price_change: float = MIN_PRICE_CHANGE_PCT,
        cooldown_minutes: int = COOLDOWN_MINUTES,
    ):
        self.symbols = set(symbols)
        self.window = dt.timedelta(minutes=window_minutes)
        self.cooldown = dt.timedelta(minutes=cooldown_minutes)
        self.min_total = min_total_usd
        self.min_lopsided = min_lopsided
        self.min_price_change = min_price_change

        # 심볼별 청산 이벤트 윈도우 (5분)
        self._liq_window: dict[str, deque[_LiqEvent]] = {
            s: deque() for s in symbols
        }
        # 심볼별 가격 스냅샷 (30초 간격, 5분 + 여유 = 12개)
        self._price_snaps: dict[str, deque[tuple[dt.datetime, float]]] = {
            s: deque() for s in symbols
        }
        self._last_snap_ts: dict[str, dt.datetime] = {}
        # 마지막 발사 시각 (쿨다운)
        self._last_fired: dict[str, dt.datetime] = {}

    def update_symbols(self, new_symbols: Iterable[str]) -> tuple[set[str], set[str]]:
        """심볼 풀 갱신. (added, removed) 반환."""
        new_set = set(new_symbols)
        added = new_set - self.symbols
        removed = self.symbols - new_set
        # 새 심볼 빈 deque 초기화
        for s in added:
            self._liq_window.setdefault(s, deque())
            self._price_snaps.setdefault(s, deque())
        # 제거된 심볼 상태도 정리 (메모리)
        for s in removed:
            self._liq_window.pop(s, None)
            self._price_snaps.pop(s, None)
            self._last_snap_ts.pop(s, None)
            self._last_fired.pop(s, None)
        self.symbols = new_set
        return added, removed

    # ── mark price 입력 (markPrice 스트림에서 호출) ───────
    def on_mark(self, symbol: str, mark: float, ts: dt.datetime) -> None:
        if symbol not in self.symbols:
            return
        last = self._last_snap_ts.get(symbol)
        if last is None or (ts - last).total_seconds() >= 30:
            snaps = self._price_snaps[symbol]
            snaps.append((ts, mark))
            # 6분 이상 오래된 건 제거
            cutoff = ts - dt.timedelta(minutes=6)
            while snaps and snaps[0][0] < cutoff:
                snaps.popleft()
            self._last_snap_ts[symbol] = ts

    # ── force order 입력 ──────────────────────────────────
    def on_force_order(self, msg) -> dict | None:
        """forceOrder 메시지 처리. 캐스케이드 발사 시 시그널 dict 반환, 아니면 None.

        메시지 구조: {"e":"forceOrder","E":ts,"o":{"s":sym,"S":side,"ap":avg_price,"z":filled_qty,...}}
        S: "SELL" = 롱 청산 (longs forced out), "BUY" = 숏 청산
        """
        o = msg.get("o") if isinstance(msg, dict) else None
        if not isinstance(o, dict):
            return None
        sym = o.get("s")
        if not sym or sym not in self.symbols:
            return None
        try:
            avg_px = float(o.get("ap", 0))
            qty = float(o.get("z", 0))    # filled qty
        except (TypeError, ValueError):
            return None
        if avg_px <= 0 or qty <= 0:
            return None

        side_raw = o.get("S")  # SELL=long liq, BUY=short liq
        if side_raw == "SELL":
            liq_side = "long"
        elif side_raw == "BUY":
            liq_side = "short"
        else:
            return None

        usd = avg_px * qty
        ts = dt.datetime.now(dt.timezone.utc)

        # 윈도우 추가 + 오래된 거 제거
        dq = self._liq_window[sym]
        dq.append(_LiqEvent(ts=ts, side=liq_side, usd=usd))
        cutoff = ts - self.window
        while dq and dq[0].ts < cutoff:
            dq.popleft()

        # 임계 체크
        last_fired = self._last_fired.get(sym)
        if last_fired is not None and (ts - last_fired) < self.cooldown:
            return None

        total = sum(e.usd for e in dq)
        if total < self.min_total:
            return None

        long_usd = sum(e.usd for e in dq if e.side == "long")
        short_usd = sum(e.usd for e in dq if e.side == "short")
        if total <= 0:
            return None
        long_ratio = long_usd / total
        short_ratio = short_usd / total
        if max(long_ratio, short_ratio) < self.min_lopsided:
            return None
        lopsided_side = "long" if long_ratio > short_ratio else "short"

        # 가격 변화
        snaps = self._price_snaps.get(sym)
        if not snaps or len(snaps) < 2:
            return None
        # 윈도우 시작 가까운 스냅
        start_snap = None
        for snap_ts, snap_px in snaps:
            if (ts - snap_ts) >= self.window - dt.timedelta(seconds=30):
                start_snap = (snap_ts, snap_px)
                break
        if start_snap is None:
            start_snap = snaps[0]
        latest_px = snaps[-1][1]
        if start_snap[1] <= 0:
            return None
        price_change = (latest_px - start_snap[1]) / start_snap[1]
        if abs(price_change) < self.min_price_change:
            return None

        # 캐스케이드 방향과 가격 변화 방향 일치성 체크
        # 롱 청산 다수 → 가격 하락 → price_change < 0 이어야 자연스러움
        if lopsided_side == "long" and price_change > 0:
            return None
        if lopsided_side == "short" and price_change < 0:
            return None

        # 발사: 반대 방향 진입
        entry_side = "long" if lopsided_side == "long" else "short"

        self._last_fired[sym] = ts
        return {
            "symbol": sym,
            "side": entry_side,
            "mark": latest_px,
            "total_liq_usd": total,
            "lopsided_side": lopsided_side,
            "lopsided_pct": max(long_ratio, short_ratio) * 100,
            "price_change_pct": price_change * 100,
            "window_minutes": int(self.window.total_seconds() / 60),
        }
