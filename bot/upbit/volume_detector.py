"""거래량 spike 감지 — REST 1분 캔들 기반.

매 N초마다 모든 모니터 마켓의 최근 21개 1분 캔들 가져옴:
- 현재 (가장 최근) 캔들: 현재 진행 중인 1분 거래량
- 직전 20개 캔들: 평균 1분 거래량 계산
- 현재 / 평균 ≥ VOLUME_SPIKE_MULT 이면 spike 발사

쿨다운: 같은 마켓 N분 내 재발사 금지.

REST 폴링 = 30초마다 30 마켓 = 60 호출/분. Upbit rate limit (10/sec, 600/min) 안.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Iterable

from .config import (
    CANDLE_POLL_INTERVAL_SEC,
    COOLDOWN_MINUTES,
    MIN_AVG_VOL_KRW,
    VOLUME_AVG_MINUTES,
    VOLUME_SPIKE_MULT,
)
from .upbit_rest import get_minute_candles

log = logging.getLogger(__name__)


class VolumeSpikeDetector:
    """주기적 폴링으로 거래량 spike 감지. 발사 시 콜백 호출."""

    def __init__(
        self,
        markets: Iterable[str],
        spike_mult: float = VOLUME_SPIKE_MULT,
        avg_minutes: int = VOLUME_AVG_MINUTES,
        min_avg_vol_krw: float = MIN_AVG_VOL_KRW,
        cooldown_minutes: int = COOLDOWN_MINUTES,
    ) -> None:
        self.markets = set(markets)
        self.spike_mult = spike_mult
        self.avg_minutes = avg_minutes
        self.min_avg_vol_krw = min_avg_vol_krw
        self.cooldown = dt.timedelta(minutes=cooldown_minutes)
        # 마지막 발사 시각 (쿨다운)
        self._last_fired: dict[str, dt.datetime] = {}

    def update_markets(self, new_markets: Iterable[str]) -> tuple[set, set]:
        new_set = set(new_markets)
        added = new_set - self.markets
        removed = self.markets - new_set
        for m in removed:
            self._last_fired.pop(m, None)
        self.markets = new_set
        return added, removed

    async def poll_once(self) -> list[dict]:
        """한 번 모든 마켓 캔들 폴링. 발사 시그널 list 반환."""
        signals = []
        # 순차 호출 (rate limit 안전). 필요 시 asyncio.gather 로 병렬화 가능
        for market in sorted(self.markets):
            try:
                sig = await self._check_market(market)
                if sig:
                    signals.append(sig)
            except Exception as e:
                log.warning("[%s] poll 실패: %s", market, e)
            # rate limit (10/sec) 보호 — 100ms sleep
            await asyncio.sleep(0.1)
        return signals

    async def _check_market(self, market: str) -> dict | None:
        # 5분 전 가격 비교를 위해 추가로 5봉 더 가져옴
        candles = await get_minute_candles(market, count=self.avg_minutes + 6)
        if len(candles) < self.avg_minutes + 1:
            return None
        cur = candles[0]
        prior = candles[1: self.avg_minutes + 1]

        try:
            cur_vol_krw = float(cur.get("candle_acc_trade_price", 0))
            cur_close = float(cur.get("trade_price", 0))
            cur_open = float(cur.get("opening_price", cur_close))
            cur_high = float(cur.get("high_price", cur_close))
            cur_low = float(cur.get("low_price", cur_close))
        except (TypeError, ValueError):
            return None

        if cur_close <= 0:
            return None

        prior_vols = []
        for c in prior:
            try:
                prior_vols.append(float(c.get("candle_acc_trade_price", 0)))
            except (TypeError, ValueError):
                pass
        if not prior_vols:
            return None

        avg_vol = sum(prior_vols) / len(prior_vols)
        if avg_vol < self.min_avg_vol_krw:
            return None

        ratio = cur_vol_krw / avg_vol if avg_vol > 0 else 0
        if ratio < self.spike_mult:
            return None

        # ── 방향 필터 1: 강한 양봉만 인정 ────────────────
        # 음봉이거나 약한 양봉 (위꼬리 큰) = 매수 신호 아님
        candle_range = cur_high - cur_low
        if candle_range <= 0:
            return None
        body_ratio = abs(cur_close - cur_open) / candle_range
        # 종가가 캔들 범위 어디에 위치하나 (0=저점, 1=고점)
        close_pos = (cur_close - cur_low) / candle_range
        is_strong_bull = (
            cur_close > cur_open       # 양봉
            and close_pos >= 0.5       # 종가가 상단 절반
            and body_ratio >= 0.3      # 몸통이 범위의 30% 이상
        )
        if not is_strong_bull:
            log.debug("[%s] spike ×%.1f BUT 약한 양봉/음봉 → 스킵 "
                      "(body=%.2f, close_pos=%.2f)",
                      market, ratio, body_ratio, close_pos)
            return None

        # ── 방향 필터 2: 5분간 하락 중이면 무시 ────────────
        # 폭락 중 거래량 폭발 = 떨어지는 칼날. 매수하면 더 손실
        pct_5min = 0.0
        if len(candles) >= 6:
            try:
                price_5min_ago = float(candles[5].get("trade_price", cur_close))
            except (TypeError, ValueError):
                price_5min_ago = cur_close
            if price_5min_ago > 0:
                pct_5min = (cur_close - price_5min_ago) / price_5min_ago
        if pct_5min < -0.005:    # -0.5% 이상 하락
            log.debug("[%s] spike ×%.1f BUT 5분 하락 %.2f%% → 스킵",
                      market, ratio, pct_5min * 100)
            return None

        # 쿨다운
        now = dt.datetime.now(dt.timezone.utc)
        last = self._last_fired.get(market)
        if last is not None and (now - last) < self.cooldown:
            return None
        self._last_fired[market] = now

        return {
            "market": market,
            "trade_price": cur_close,
            "open_price": cur_open,
            "is_bullish": True,
            "body_ratio": body_ratio,
            "close_pos": close_pos,
            "pct_5min": pct_5min * 100,
            "cur_vol_krw": cur_vol_krw,
            "avg_vol_krw": avg_vol,
            "ratio": ratio,
        }

    async def run_periodically(self, on_signal_callback):
        """무한 루프 — N초마다 polling, 시그널 발생 시 콜백 호출."""
        log.info("VolumeSpikeDetector 폴링 시작 (%d초 주기, mult=%.1f, avg=%d분)",
                 CANDLE_POLL_INTERVAL_SEC, self.spike_mult, self.avg_minutes)
        while True:
            try:
                signals = await self.poll_once()
                for sig in signals:
                    try:
                        await on_signal_callback(sig)
                    except Exception as e:
                        log.exception("on_signal_callback error: %s", e)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("poll_once error: %s", e)
            await asyncio.sleep(CANDLE_POLL_INTERVAL_SEC)
