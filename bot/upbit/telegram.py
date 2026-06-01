"""Upbit 봇 Telegram 전송 — UPBIT_TELEGRAM_BOT_TOKEN 사용 (Binance 봇과 분리)."""

from __future__ import annotations

import logging

import httpx

from .config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS

log = logging.getLogger(__name__)
_TG = "https://api.telegram.org"


async def send(text: str, parse_mode: str = "Markdown") -> None:
    if not TELEGRAM_BOT_TOKEN:
        log.warning("UPBIT_TELEGRAM_BOT_TOKEN 미설정 — 미전송: %s", text[:80])
        return
    if not TELEGRAM_CHAT_IDS:
        log.warning("UPBIT_TELEGRAM_CHAT_IDS 미설정 — 미전송")
        return
    url = f"{_TG}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=10.0) as client:
        for chat_id in TELEGRAM_CHAT_IDS:
            try:
                r = await client.post(url, json={
                    "chat_id": chat_id, "text": text,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                })
                if r.status_code != 200:
                    log.error("Telegram %s 실패 (%s): %s",
                              chat_id, r.status_code, r.text[:200])
            except Exception as e:
                log.error("Telegram %s 에러: %s", chat_id, e)


async def send_to(chat_id: str, text: str, parse_mode: str = "Markdown") -> bool:
    if not TELEGRAM_BOT_TOKEN:
        return False
    url = f"{_TG}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.post(url, json={
                "chat_id": chat_id, "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            })
            return r.status_code == 200
        except Exception as e:
            log.error("send_to %s 에러: %s", chat_id, e)
            return False


async def get_updates(offset: int, timeout: int = 25) -> list[dict]:
    if not TELEGRAM_BOT_TOKEN:
        return []
    url = f"{_TG}/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    async with httpx.AsyncClient(timeout=timeout + 10) as client:
        try:
            r = await client.get(url, params={
                "offset": offset, "timeout": timeout,
                "allowed_updates": '["message"]',
            })
            if r.status_code != 200:
                log.warning("getUpdates HTTP %s", r.status_code)
                return []
            data = r.json()
            if not data.get("ok"):
                return []
            return data.get("result", []) or []
        except httpx.ReadTimeout:
            return []
        except Exception as e:
            log.error("getUpdates 에러: %s", e)
            return []
