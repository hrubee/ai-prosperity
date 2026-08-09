"""Minimal fire-and-forget Telegram notifier for the F&O (NIFTY) brain.

Reuses the go-trader bot token (TELEGRAM_BOT_TOKEN) and sends to TELEGRAM_CHAT_ID
(falls back to TELEGRAM_OWNER_ID). Every function swallows all errors — a Telegram
hiccup must NEVER delay or break the trading loop. No-op when creds are absent.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("tg-notify")


def _creds() -> tuple[str | None, str | None]:
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_OWNER_ID") or "").strip()
    if token and chat:
        return token, chat
    return None, None


def send_message(text: str) -> None:
    token, chat = _creds()
    if not token:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat, "text": text[:4000], "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:  # pragma: no cover — notify must never raise
        log.warning("telegram send_message failed: %s", e)


def send_photo(path: str, caption: str = "") -> None:
    token, chat = _creds()
    if not token or not path or not os.path.exists(path):
        return
    try:
        import requests
        with open(path, "rb") as photo:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                data={"chat_id": chat, "caption": caption[:1024], "parse_mode": "HTML"},
                files={"photo": photo},
                timeout=12,
            )
    except Exception as e:  # pragma: no cover — notify must never raise
        log.warning("telegram send_photo failed: %s", e)
