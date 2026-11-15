"""SMC Notebook Bot — escanea TODOS los perpetuos USDT de BingX, avisa por Telegram y (opcional) opera."""
from __future__ import annotations

import asyncio
import html
import logging
import signal as os_signal
import sys
import time

from . import __version__
from .bingx import BingX, BingXError
from .config import Config, load
from .strategy import Params, Signal, SMCEngine
from .telegram import Telegram
from .trader import Trader

log = logging.getLogger("smc")


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.bx = BingX(cfg.bingx_key, cfg.bingx_secret, cfg.req_per_sec)
        self.tg = Telegram(cfg.tg_token, cfg.tg_chat)
        self.trader = Trader(cfg, self.bx)
        self.params = Params(
            swing_len=cfg.swing_len, entry_mode=cfg.entry_mode, ob_mitigation=cfg.ob_mitigation,
            cooldown_bars=cfg.cooldown_bars, sl_buffer_atr=cfg.sl_buffer_atr, rr_target=cfg.rr_target,
            use_liquidity_tp=cfg.use_liquidity_tp, min_rr=cfg.min_rr, max_risk_pct=cfg.max_risk_pct,
            require_sweep=cfg.require_sweep, sweep_lookback=cfg.sweep_lookback, ob_min_age=cfg.ob_min_age,
        )
        self.engines: dict[str, SMCEngine] = {}
        self.symbols: list[str] = []
        self.trade_lock = asyncio.Lock()
        self.sent: set = set()
        self.stats = {"scans": 0, "signals": 0, "trades": 0, "errors": 0}
        self.auto_trade = cfg.auto_trade
        self.stop = asyncio.Event()

    # ------------------------------------------------------------------
    async def refresh_universe(self):
        cfg = self.cfg
        cons, ticks = await asyncio.gather(self.bx.contracts(), self.bx.tickers())
        vol = {}
        for t in ticks:
            try:
                vol[t.get("symbol")] = float(t.get("quoteVolume") or 0)
            except (TypeError, ValueError):
                pass
        use_vol = any(v > 0 for v in vol.values())
        info, rows = {}, []
        for c in cons:
            s = c.get("symbol", "")
            if not s.endswith("-USDT"):
                continue
            try:
                if int(c.get("status", 1)) != 1:
                    continue
            except (TypeError, ValueError):
                pass
            info[s] = c
            v = vol.get(s, 0.0)
            if use_vol and v < cfg.min_volume:
                continue
            if cfg.include and s not in cfg.include:
                continue
            if s in cfg.exclude:
                continue
            rows.append((v, s))
        rows.sort(reverse=True)
        if cfg.max_symbols:
            rows = rows[:cfg.max_symbols]
        self.symbols = [s for _, s in rows]
        self.trader.info = info
        for s in list(self.engines):
            if s not in self.symbols:
                del self.engines[s]
        log.info("Universo: %d símbolos (de %d contratos USDT)", len(self.symbols), len(info))

    # ------------------------------------------------------------------
    async def process(self, sym: str, now_ms: int):
        cfg, tf = self.cfg, self.cfg.tf_ms
        last_open = (now_ms // tf) * tf - tf   # apertura de la última vela cerrada
        eng = self.engines.get(sym)
        emit = eng is not None
        limit = cfg.warmup
        if eng is not None and eng.last_t is not None:
            gap = (last_open - eng.last_t) // tf
            if gap <= 0:
                return
            if gap > cfg.warmup - 60:
                eng, emit = None, False
            else:
                limit = min(1000, gap + 2)
        if eng is None:
            eng = SMCEngine(self.params)
        candles = await self.bx.klines(sym, cfg.timeframe, limit)
        for c in candles:
            if c.t + tf > now_ms:           # vela en formación
                continue
            if eng.last_t is not None and c.t <= eng.last_t:
                continue
            sig = eng.on_candle(c)
            if sig and emit and c.t == last_open:
                await self.on_signal(sym, sig)
        self.engines[sym] = eng

    # ------------------------------------------------------------------
    def fmt_signal(self, sym: str, s: Signal) -> str:
        pp = int(self.trader.info.get(sym, {}).get("pricePrecision", 6))
        f = lambda x: f"{x:.{pp}f}"
        e = "🟢" if s.side == "LONG" else "🔴"
        sl_pct = (s.sl - s.entry) / s.entry * 100
        tp_pct = (s.tp - s.entry) / s.entry * 100
        return (
            f"{e} <b>{s.side}</b> · <b>{html.escape(sym)}</b> · {self.cfg.timeframe}\n"
            f"Entrada: <code>{f(s.entry)}</code>\n"
            f"SL: <code>{f(s.sl)}</code> ({sl_pct:+.2f}%)\n"
            f"TP: <code>{f(s.tp)}</code> ({tp_pct:+.2f}%) · R:R <b>{s.rr:.2f}</b> ({'liquidez' if s.tp_kind == 'LIQ' else 'R fijo'})\n"
            f"OB: {f(s.ob_bot)} – {f(s.ob_top)} · origen {s.ob_kind} · barrida previa: {'sí' if s.swept else 'no'}\n"
            f"Modo: {s.mode}"
        )

    async def on_signal(self, sym: str, s: Signal):
        key = (sym, s.bar_time)
        if key in self.sent:
            return
        self.sent.add(key)
        if len(self.sent) > 5000:
            self.sent = set(list(self.sent)[-2000:])
        self.stats["signals"] += 1
        text = self.fmt_signal(sym, s)
        async with self.trade_lock:
            if self.auto_trade:
                try:
                    ok, msg = await self.trader.execute(sym, s)
                except Exception as ex:  # noqa
                    ok, msg = False, f"error: {ex}"
                    log.exception("execute %s", sym)
                if ok:
                    self.stats["trades"] += 1
                text += f"\n\n{'✅ <b>OPERADA</b>' if ok else '⚠️ <b>NO OPERADA</b>'} — {html.escape(msg)}"
            else:
                text += "\n\n<i>Solo señal (AUTO_TRADE=false)</i>"
            await self.tg.send(text)
        log.info("SEÑAL %s %s @ %s", s.side, sym, s.entry)

    # ------------------------------------------------------------------
    async def scan(self):
        now_ms = int(time.time() * 1000)
        t0 = time.time()
        res = await asyncio.gather(*(self.process(s, now_ms) for s in self.symbols),
                                   return_exceptions=True)
        errs = [r for r in res if isinstance(r, Exception)]
        self.stats["errors"] += len(errs)
        self.stats["scans"] += 1
        if errs:
            log.warning("%d errores en scan (ej: %r)", len(errs), errs[0])
        log.info("Scan OK: %d símbolos en %.1fs", len(self.symbols), time.time() - t0)
        return len(errs)

    async def run(self):
        cfg = self.cfg
        await self.bx.start()
        await self.tg.start()
        if self.auto_trade:
            if not (cfg.bingx_key and cfg.bingx_secret):
                log.error("AUTO_TRADE=true sin claves BingX → modo solo señales")
                self.auto_trade = False
            else:
                try:
                    await self.trader.init()
                    b = await self.bx.balance()
                    log.info("Balance futuros: %.2f USDT", b["equity"])
                except Exception as e:  # noqa
                    log.error("No se pudo validar la API de BingX (%s) → modo solo señales", e)
                    self.auto_trade = False
        await self.refresh_universe()
        await self.tg.send(
            f"🤖 <b>SMC Notebook Bot v{__version__}</b> iniciado\n"
            f"TF {cfg.timeframe} · {len(self.symbols)} símbolos · vol24h ≥ {cfg.min_volume:,.0f} USDT\n"
            f"Modo: {'<b>OPERANDO</b> ' + f'(riesgo {cfg.risk_pct}% · x{cfg.leverage} · máx {cfg.max_positions} pos)' if self.auto_trade else 'solo señales'}\n"
            f"Entrada: {cfg.entry_mode} · R objetivo {cfg.rr_target} · TP liquidez: {'sí' if cfg.use_liquidity_tp else 'no'}")
        log.info("Calentando %d símbolos…", len(self.symbols))
        await self.scan()
        last_uni = last_hb = time.time()

        while not self.stop.is_set():
            tf = cfg.tf_ms / 1000
            nxt = (int(time.time() // tf) + 1) * tf + cfg.scan_delay
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=max(0.0, nxt - time.time()))
                break
            except asyncio.TimeoutError:
                pass
            try:
                if time.time() - last_uni > cfg.universe_refresh_min * 60:
                    await self.refresh_universe()
                    last_uni = time.time()
                await self.scan()
            except Exception as e:  # noqa
                log.exception("scan falló")
                self.stats["errors"] += 1
                await self.tg.send(f"⚠️ Error en scan: {html.escape(str(e))[:300]}")
            if cfg.heartbeat_hours > 0 and time.time() - last_hb > cfg.heartbeat_hours * 3600:
                last_hb = time.time()
                st = self.stats
                await self.tg.send(
                    f"💓 Activo · {len(self.symbols)} símbolos · scans {st['scans']} · "
                    f"señales {st['signals']} · operadas {st['trades']} · errores {st['errors']}")

        await self.bx.close()
        await self.tg.close()


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load()
    app = App(cfg)

    async def runner():
        loop = asyncio.get_running_loop()
        for sig in (os_signal.SIGTERM, os_signal.SIGINT):
            try:
                loop.add_signal_handler(sig, app.stop.set)
            except NotImplementedError:
                pass
        await app.run()

    asyncio.run(runner())


if __name__ == "__main__":
    main()
