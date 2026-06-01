"""Upbit WebSocket ticker stream.

연결 후 구독 메시지 보내야 함:
    [
      {"ticket": "<uuid>"},
      {"type": "ticker", "codes": ["KRW-BTC", ...], "isOnlyRealtime": true},
      {"format": "DEFAULT"}
    ]

메시지는 binary frame (UTF-8 JSON).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Iterable

import websockets

from .config import UPBIT_WS

log = logging.getLogger(__name__)


async def ticker_stream(codes: Iterable[str]):
    """ticker 메시지 yield. 자동 재연결."""
    codes_list = list(codes)
    backoff = 1.0
    while True:
        try:
            log.info("Upbit WS 연결: %s (markets=%d)", UPBIT_WS, len(codes_list))
            async with websockets.connect(
                UPBIT_WS, ping_interval=180, ping_timeout=60, close_timeout=10,
            ) as ws:
                # 구독
                sub = [
                    {"ticket": str(uuid.uuid4())[:12]},
                    {"type": "ticker", "codes": codes_list, "isOnlyRealtime": True},
                    {"format": "DEFAULT"},
                ]
                await ws.send(json.dumps(sub))
                log.info("Upbit WS 구독 완료 — 메시지 대기")
                backoff = 1.0
                async for raw in ws:
                    try:
                        if isinstance(raw, (bytes, bytearray)):
                            text = raw.decode("utf-8")
                        else:
                            text = raw
                        msg = json.loads(text)
                        yield msg
                    except Exception as e:
                        log.warning("Upbit WS parse error: %s", e)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("Upbit WS 에러: %s — %.1fs 후 재연결", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
