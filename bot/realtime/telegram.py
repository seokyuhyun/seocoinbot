"""Telegram Bot API — 메시지 전송. 복수 chat_id 동시 전송."""

from __future__ import annotations

import logging

import httpx

from .config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS

log = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org"


async def send(text: str, parse_mode: str = "Markdown") -> None:
    """등록된 모든 chat_id 에 같은 메시지 전송. 실패해도 다른 chat 진행."""
    if not TELEGRAM_BOT_TOKEN:
        log.warning("TELEGRAM_BOT_TOKEN 미설정 — 알림 미전송: %s", text[:80])
        return
    if not TELEGRAM_CHAT_IDS:
        log.warning("TELEGRAM_CHAT_IDS 미설정 — 알림 미전송")
        return

    url = f"{_TELEGRAM_API}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=10.0) as client:
        for chat_id in TELEGRAM_CHAT_IDS:
            try:
                r = await client.post(
                    url,
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": parse_mode,
                        "disable_web_page_preview": True,
                    },
                )
                if r.status_code != 200:
                    log.error("Telegram %s 실패 (%s): %s",
                              chat_id, r.status_code, r.text[:200])
            except Exception as e:
                log.error("Telegram %s 에러: %s", chat_id, e)


async def send_plain(text: str) -> None:
    """Markdown 파싱 실패 위험 있을 때 일반 텍스트 전송."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_IDS:
        log.warning("Telegram 미설정 — 전송 안 함")
        return
    url = f"{_TELEGRAM_API}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=10.0) as client:
        for chat_id in TELEGRAM_CHAT_IDS:
            try:
                await client.post(url, json={"chat_id": chat_id, "text": text})
            except Exception as e:
                log.error("Telegram(plain) %s 에러: %s", chat_id, e)


async def send_to(chat_id: str, text: str, parse_mode: str = "Markdown") -> bool:
    """단일 chat_id 에 전송 (명령어 응답용)."""
    if not TELEGRAM_BOT_TOKEN:
        return False
    url = f"{_TELEGRAM_API}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            })
            return r.status_code == 200
        except Exception as e:
            log.error("Telegram send_to %s 에러: %s", chat_id, e)
            return False


async def get_updates(offset: int, timeout: int = 25) -> list[dict]:
    """long-polling getUpdates. offset = next update_id 시작점."""
    if not TELEGRAM_BOT_TOKEN:
        return []
    url = f"{_TELEGRAM_API}/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    # 클라이언트 timeout 은 server timeout 보다 길게
    async with httpx.AsyncClient(timeout=timeout + 10) as client:
        try:
            r = await client.get(
                url,
                params={
                    "offset": offset,
                    "timeout": timeout,
                    "allowed_updates": '["message"]',
                },
            )
            if r.status_code != 200:
                log.warning("getUpdates HTTP %s", r.status_code)
                return []
            data = r.json()
            if not data.get("ok"):
                log.warning("getUpdates not ok: %s", data)
                return []
            return data.get("result", []) or []
        except httpx.ReadTimeout:
            return []
        except Exception as e:
            log.error("getUpdates 에러: %s", e)
            return []
