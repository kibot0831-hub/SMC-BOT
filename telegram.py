import asyncio
import logging

import aiohttp

log = logging.getLogger("telegram")


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat = chat_id
        self._s: aiohttp.ClientSession | None = None

    @property
    def enabled(self):
        return bool(self.token and self.chat)

    async def start(self):
        self._s = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))

    async def close(self):
        if self._s:
            await self._s.close()

    async def send(self, text: str):
        if not self.enabled:
            log.info("TG desactivado | %s", text.replace("\n", " | "))
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        chunks = [text[i:i + 3900] for i in range(0, len(text), 3900)] or [""]
        for ch in chunks:
            payload = {"chat_id": self.chat, "text": ch, "parse_mode": "HTML",
                       "disable_web_page_preview": True}
            for attempt in range(3):
                try:
                    async with self._s.post(url, json=payload) as r:
                        if r.status == 429:
                            try:
                                ra = (await r.json()).get("parameters", {}).get("retry_after", 3)
                            except Exception:
                                ra = 3
                            await asyncio.sleep(ra + 1)
                            continue
                        if r.status != 200:
                            log.warning("Telegram %s: %s", r.status, (await r.text())[:200])
                        break
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    log.warning("Telegram error: %s", e)
                    await asyncio.sleep(2)
