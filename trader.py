"""Ejecución en BingX: entrada a mercado + SL (STOP_MARKET) + TP (TAKE_PROFIT_MARKET)."""
from __future__ import annotations

import logging
import math

from .bingx import BingX, BingXError
from .config import Config
from .strategy import Signal

log = logging.getLogger("trader")


def floor_to(x: float, prec: int) -> float:
    f = 10 ** prec
    return math.floor(x * f + 1e-9) / f


class Trader:
    def __init__(self, cfg: Config, bx: BingX):
        self.cfg = cfg
        self.bx = bx
        self.info: dict = {}
        self.hedge: bool | None = None
        self._margin_done: set = set()
        self._lev_done: set = set()

    async def init(self):
        self.hedge = await self.bx.position_mode()
        log.info("Modo posición: %s", "HEDGE" if self.hedge else "ONE-WAY")

    def fmt_price(self, sym, x):
        pp = int(self.info.get(sym, {}).get("pricePrecision", 6))
        return f"{x:.{pp}f}"

    async def execute(self, sym: str, sig: Signal) -> tuple[bool, str]:
        cfg, bx = self.cfg, self.bx
        if self.hedge is None:
            await self.init()

        pos = await bx.positions()
        open_syms = {p.get("symbol") for p in pos if abs(float(p.get("positionAmt") or 0)) > 0}
        if sym in open_syms:
            return False, "ya hay posición abierta en este símbolo"
        if len(open_syms) >= cfg.max_positions:
            return False, f"máximo de posiciones abiertas ({cfg.max_positions})"

        bal = await bx.balance()
        equity, avail = bal["equity"], bal["available"]
        if equity <= 0:
            return False, "balance de futuros = 0"

        ci = self.info.get(sym, {})
        qp = int(ci.get("quantityPrecision", 3))
        min_qty = float(ci.get("tradeMinQuantity") or 0)
        min_usdt = float(ci.get("tradeMinUSDT") or 0)
        lev = cfg.leverage
        try:
            mx = int(float(ci.get("maxLongLeverage") or 0)) if sig.side == "LONG" else int(float(ci.get("maxShortLeverage") or 0))
            if mx > 0:
                lev = min(lev, mx)
        except (TypeError, ValueError):
            pass

        entry = sig.entry
        risk_unit = abs(entry - sig.sl)
        if cfg.fixed_margin_usdt > 0:
            qty = cfg.fixed_margin_usdt * lev / entry
        else:
            qty = equity * cfg.risk_pct / 100 / risk_unit
        qty = min(qty, equity * cfg.max_margin_pct / 100 * lev / entry)
        qty = min(qty, avail * 0.95 * lev / entry)
        qty = floor_to(qty, qp)
        if qty <= 0 or qty < min_qty or qty * entry < min_usdt:
            return False, f"tamaño ({qty}) por debajo del mínimo del contrato"

        # margen aislado + apalancamiento (best effort)
        if sym not in self._margin_done:
            try:
                await bx.set_margin_type(sym, cfg.margin_mode)
            except BingXError as e:
                log.info("marginType %s: %s", sym, e)
            self._margin_done.add(sym)
        lev_side = sig.side if self.hedge else "BOTH"
        if (sym, lev_side, lev) not in self._lev_done:
            try:
                await bx.set_leverage(sym, lev_side, lev)
                self._lev_done.add((sym, lev_side, lev))
            except BingXError as e:
                log.warning("leverage %s: %s", sym, e)

        side = "BUY" if sig.side == "LONG" else "SELL"
        close_side = "SELL" if sig.side == "LONG" else "BUY"
        ps = sig.side if self.hedge else "BOTH"
        qstr = f"{qty:.{qp}f}"
        extra = {} if self.hedge else {"reduceOnly": "true"}

        try:
            await bx.place_order(symbol=sym, side=side, positionSide=ps, type="MARKET", quantity=qstr)
        except BingXError as e:
            return False, f"orden rechazada: {e.msg}"

        # SL obligatorio: si falla, se cierra la posición
        try:
            await bx.place_order(symbol=sym, side=close_side, positionSide=ps, type="STOP_MARKET",
                                 quantity=qstr, stopPrice=self.fmt_price(sym, sig.sl),
                                 workingType="MARK_PRICE", **extra)
        except BingXError as e:
            try:
                await bx.place_order(symbol=sym, side=close_side, positionSide=ps, type="MARKET",
                                     quantity=qstr, **extra)
                return False, f"SL rechazado ({e.msg}); posición CERRADA por seguridad"
            except BingXError as e2:
                return False, f"⚠️ SL rechazado ({e.msg}) y cierre falló ({e2.msg}). REVISA {sym} MANUALMENTE"

        tp_note = ""
        try:
            await bx.place_order(symbol=sym, side=close_side, positionSide=ps, type="TAKE_PROFIT_MARKET",
                                 quantity=qstr, stopPrice=self.fmt_price(sym, sig.tp),
                                 workingType="MARK_PRICE", **extra)
        except BingXError as e:
            tp_note = f" ⚠️ TP rechazado ({e.msg}); solo SL activo."

        return True, f"qty {qstr} · x{lev} · riesgo ≈{qty * risk_unit:.2f} USDT{tp_note}"
