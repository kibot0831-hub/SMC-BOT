"""Cliente mínimo BingX Perpetual Futures (REST, aiohttp)."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time

import aiohttp
from yarl import URL

from .strategy import Candle

log = logging.getLogger("bingx")


class BingXError(Exception):
    def __init__(self, code, msg):
        super().__init__(f"BingX {code}: {msg}")
        self.code = code
        self.msg = msg


class RateLimiter:
    def __init__(self, rps: float):
        self.interval = 1.0 / max(rps, 0.5)
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self):
        async with self._lock:
            now = time.monotonic()
            if now < self._next:
                await asyncio.sleep(self._next - now)
                now = self._next
            self._next = now + self.interval


class BingX:
    BASE = "https://open-api.bingx.com"

    def __init__(self, key: str, secret: str, rps: float = 8.0):
        self.key = key
        self.secret = secret
        self.rl = RateLimiter(rps)
        self._s: aiohttp.ClientSession | None = None

    async def start(self):
        headers = {"X-BX-APIKEY": self.key} if self.key else {}
        self._s = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25), headers=headers)

    async def close(self):
        if self._s:
            await self._s.close()

    # ------------------------------------------------------------------
    async def _request(self, method, path, params=None, signed=False, limited=False, retries=3):
        last = None
        for attempt in range(retries):
            p = dict(params or {})
            if signed:
                p["timestamp"] = int(time.time() * 1000)
                p["recvWindow"] = 5000
            qs = "&".join(f"{k}={p[k]}" for k in sorted(p))
            if signed:
                sig = hmac.new(self.secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
                qs += f"&signature={sig}"
            url = URL(f"{self.BASE}{path}" + (f"?{qs}" if qs else ""), encoded=True)
            if limited:
                await self.rl.wait()
            try:
                async with self._s.request(method, url) as r:
                    txt = await r.text()
                    if r.status == 429:
                        await asyncio.sleep(2 + attempt * 2)
                        last = BingXError(429, "rate limit HTTP")
                        continue
                    data = json.loads(txt)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
                last = e
                await asyncio.sleep(1 + attempt)
                continue
            code = int(data.get("code", 0) or 0)
            if code == 0:
                return data.get("data")
            msg = str(data.get("msg", ""))
            if code == 100410 or "rate limit" in msg.lower():
                await asyncio.sleep(3 + attempt * 3)
                last = BingXError(code, msg)
                continue
            raise BingXError(code, msg)
        raise last if last else RuntimeError("request failed")

    # ---------------- público ----------------
    async def contracts(self):
        return await self._request("GET", "/openApi/swap/v2/quote/contracts") or []

    async def tickers(self):
        return await self._request("GET", "/openApi/swap/v2/quote/ticker") or []

    async def klines(self, symbol: str, interval: str, limit: int):
        d = await self._request(
            "GET", "/openApi/swap/v3/quote/klines",
            {"symbol": symbol, "interval": interval, "limit": limit}, limited=True)
        out, seen = [], set()
        for k in d or []:
            if isinstance(k, dict):
                t = int(k["time"])
                cd = Candle(t, float(k["open"]), float(k["high"]), float(k["low"]),
                            float(k["close"]), float(k.get("volume", 0) or 0))
            else:
                t = int(k[0])
                cd = Candle(t, float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]))
            if t in seen:
                continue
            seen.add(t)
            out.append(cd)
        out.sort(key=lambda x: x.t)
        return out

    # ---------------- privado ----------------
    async def balance(self):
        d = await self._request("GET", "/openApi/swap/v2/user/balance", signed=True)
        b = d.get("balance", d) if isinstance(d, dict) else {}
        equity = float(b.get("equity") or b.get("balance") or 0)
        avail = float(b.get("availableMargin") or b.get("balance") or 0)
        return {"equity": equity, "available": avail}

    async def positions(self):
        return await self._request("GET", "/openApi/swap/v2/user/positions", signed=True) or []

    async def position_mode(self) -> bool:
        d = await self._request("GET", "/openApi/swap/v1/positionSide/dual", signed=True)
        v = d.get("dualSidePosition") if isinstance(d, dict) else d
        return str(v).lower() == "true"

    async def set_margin_type(self, symbol, mode):
        return await self._request("POST", "/openApi/swap/v2/trade/marginType",
                                   {"symbol": symbol, "marginType": mode}, signed=True, retries=1)

    async def set_leverage(self, symbol, side, leverage):
        return await self._request("POST", "/openApi/swap/v2/trade/leverage",
                                   {"symbol": symbol, "side": side, "leverage": leverage},
                                   signed=True, retries=1)

    async def place_order(self, **params):
        return await self._request("POST", "/openApi/swap/v2/trade/order", params,
                                   signed=True, retries=1)
