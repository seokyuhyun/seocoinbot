"""Binance USDT-M Futures WebSocket 구독 매니저.

자동 재연결 (5초 backoff), ping/pong 유지.
스트림 메시지를 async generator 로 yield.
"""

from __future__ import annotations

import asyncio
import json
import logging

import websockets

from .config import BINANCE_WS

log = logging.getLogger(__name__)


async def stream(stream_name: str):
    """단일 스트림 구독. 예: '!markPrice@arr@1s', 'btcusdt@aggTrade', 'btcusdt@depth20@100ms'.

    각 메시지를 dict 로 yield. 연결 끊기면 자동 재연결.
    """
    url = f"{BINANCE_WS}/{stream_name}"
    backoff = 1.0
    while True:
        try:
            log.info("WebSocket 연결: %s", url)
            async with websockets.connect(
                url, ping_interval=180, ping_timeout=60, close_timeout=10,
            ) as ws:
                backoff = 1.0   # 성공 시 리셋
                log.info("WebSocket 연결됨 — 메시지 수신 시작")
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        yield msg
                    except json.JSONDecodeError as e:
                        log.warning("JSON 파싱 실패: %s", e)
        except asyncio.CancelledError:
            log.info("WebSocket 취소됨")
            raise
        except Exception as e:
            log.error("WebSocket 에러: %s — %.1fs 후 재연결", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
