"""
Port 1:1 del indicador Pine v6 "SMC Notebook · Barrida → MSS → CHOCH → OB · 5m".
Motor incremental: una llamada on_candle() por vela CERRADA (equivale a barstate.isconfirmed).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional

KEEP = 200  # velas retenidas por símbolo


@dataclass
class Candle:
    t: int  # open time (ms)
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0


@dataclass
class Params:
    swing_len: int = 5
    entry_mode: str = "Retest OB"        # "Retest OB" | "Mitad del OB"
    ob_mitigation: str = "Close through"  # "Close through" | "Wick through"
    cooldown_bars: int = 8
    sl_buffer_atr: float = 0.1
    rr_target: float = 2.5
    use_liquidity_tp: bool = True
    # extras (defaults = comportamiento neutro)
    min_rr: float = 1.0
    max_risk_pct: float = 3.0
    require_sweep: bool = False
    sweep_lookback: int = 30
    ob_min_age: int = 0


@dataclass
class OB:
    top: float
    bot: float
    bar: int
    created: int
    kind: str      # "CHOCH" | "MSS"
    swept: bool
    active: bool = True


@dataclass
class Signal:
    side: str          # "LONG" | "SHORT"
    entry: float
    sl: float
    tp: float
    rr: float
    tp_kind: str       # "LIQ" | "R"
    atr: float
    ob_top: float
    ob_bot: float
    ob_kind: str
    swept: bool
    bar_time: int
    mode: str


class SMCEngine:
    def __init__(self, p: Params):
        self.p = p
        self.bars: list[Candle] = []
        self.n = -1
        self.last_t: Optional[int] = None
        # ATR (Wilder RMA 14)
        self.atr: Optional[float] = None
        self._pc: Optional[float] = None
        self._tr_n = 0
        self._tr_sum = 0.0
        # swings / barridas
        self.last_ph: Optional[float] = None
        self.last_pl: Optional[float] = None
        self.piv_highs: deque = deque(maxlen=40)
        self.piv_lows: deque = deque(maxlen=40)
        self.sweep_low_bar: Optional[int] = None
        self.sweep_high_bar: Optional[int] = None
        # estructura
        self.trend = 0
        self.last_high: Optional[float] = None
        self.last_low: Optional[float] = None
        # order blocks
        self.ob_bull: Optional[OB] = None
        self.ob_bear: Optional[OB] = None
        # cooldown
        self.last_long: Optional[int] = None
        self.last_short: Optional[int] = None

    # ------------------------------------------------------------------
    def _pivots(self):
        L = self.p.swing_len
        b = self.bars
        if len(b) < 2 * L + 1:
            return None, None
        cand = b[-1 - L]
        left = b[-1 - 2 * L:-1 - L]
        right = b[-L:]
        ph = pl = None
        if all(cand.h >= x.h for x in left) and all(cand.h > x.h for x in right):
            ph = cand.h
        if all(cand.l <= x.l for x in left) and all(cand.l < x.l for x in right):
            pl = cand.l
        return ph, pl

    # ------------------------------------------------------------------
    def on_candle(self, c: Candle) -> Optional[Signal]:
        p = self.p
        self.bars.append(c)
        if len(self.bars) > KEEP:
            del self.bars[0]
        self.n += 1
        i = self.n
        b = self.bars
        self.last_t = c.t

        # ---- ATR ----
        tr = c.h - c.l if self._pc is None else max(
            c.h - c.l, abs(c.h - self._pc), abs(c.l - self._pc))
        self._pc = c.c
        self._tr_n += 1
        if self._tr_n <= 14:
            self._tr_sum += tr
            if self._tr_n == 14:
                self.atr = self._tr_sum / 14
        else:
            self.atr = (self.atr * 13 + tr) / 14

        # ---- Swings + barridas ----
        ph, pl = self._pivots()
        if ph is not None:
            self.last_ph = ph
            self.piv_highs.append(ph)
        if pl is not None:
            self.last_pl = pl
            self.piv_lows.append(pl)
        if self.last_ph is not None and c.h > self.last_ph and c.c < self.last_ph:
            self.sweep_high_bar = i
        if self.last_pl is not None and c.l < self.last_pl and c.c > self.last_pl:
            self.sweep_low_bar = i

        # ---- Estructura (BOS/MSS/CHOCH) ----
        t, lh, ll = self.trend, self.last_high, self.last_low
        bull_bos = t <= 0 and lh is not None and c.c > lh
        bear_bos = t >= 0 and ll is not None and c.c < ll
        bull_choch = t == -1 and lh is not None and c.c > lh
        bear_choch = t == 1 and ll is not None and c.c < ll
        if bull_choch or bull_bos:
            self.trend = 1
            self.last_low = c.l
        if bear_choch or bear_bos:
            self.trend = -1
            self.last_high = c.h
        if ph is not None:
            self.last_high = ph
        if pl is not None:
            self.last_low = pl

        # ---- Order Blocks ----
        def swept(bar):
            return bar is not None and i - bar <= p.sweep_lookback

        if bull_choch or (bull_bos and self.trend == 1):
            kind = "CHOCH" if bull_choch else "MSS"
            if i >= 1 and len(b) >= 2 and b[-2].c < b[-2].o:
                self.ob_bull = OB(b[-2].h, b[-2].l, i - 1, i, kind, swept(self.sweep_low_bar))
            elif len(b) >= 3 and b[-3].c < b[-3].o:
                self.ob_bull = OB(b[-3].h, b[-3].l, i - 2, i, kind, swept(self.sweep_low_bar))
        if bear_choch or (bear_bos and self.trend == -1):
            kind = "CHOCH" if bear_choch else "MSS"
            if len(b) >= 2 and b[-2].c > b[-2].o:
                self.ob_bear = OB(b[-2].h, b[-2].l, i - 1, i, kind, swept(self.sweep_high_bar))
            elif len(b) >= 3 and b[-3].c > b[-3].o:
                self.ob_bear = OB(b[-3].h, b[-3].l, i - 2, i, kind, swept(self.sweep_high_bar))

        bull, bear = self.ob_bull, self.ob_bear
        if bull and bull.active:
            if (c.c < bull.bot) if p.ob_mitigation == "Close through" else (c.l < bull.bot):
                bull.active = False
        if bear and bear.active:
            if (c.c > bear.top) if p.ob_mitigation == "Close through" else (c.h > bear.top):
                bear.active = False

        # ---- Entrada ----
        touch_bull = bool(bull and bull.active and c.l <= bull.top and c.h >= bull.bot)
        touch_bear = bool(bear and bear.active and c.h >= bear.bot and c.l <= bear.top)
        long_e = short_e = False
        if p.entry_mode == "Retest OB":
            long_e = touch_bull and c.c > c.o and c.c > bull.bot
            short_e = touch_bear and c.c < c.o and c.c < bear.top
        else:
            long_e = touch_bull and c.c >= (bull.top + bull.bot) / 2
            short_e = touch_bear and c.c <= (bear.top + bear.bot) / 2
        if long_e and (i - bull.created < p.ob_min_age or (p.require_sweep and not bull.swept)):
            long_e = False
        if short_e and (i - bear.created < p.ob_min_age or (p.require_sweep and not bear.swept)):
            short_e = False

        sig_long = long_e and (self.last_long is None or i - self.last_long > p.cooldown_bars)
        sig_short = short_e and (self.last_short is None or i - self.last_short > p.cooldown_bars)
        if sig_long:
            self.last_long = i
            bull.active = False
        if sig_short:
            self.last_short = i
            bear.active = False
        if sig_long and sig_short:
            return None  # señales contradictorias en la misma vela
        if sig_long:
            return self._build("LONG", bull, c)
        if sig_short:
            return self._build("SHORT", bear, c)
        return None

    # ------------------------------------------------------------------
    def _build(self, side: str, ob: OB, c: Candle) -> Optional[Signal]:
        p = self.p
        atr = self.atr or 0.0
        entry = c.c
        if side == "LONG":
            sl = ob.bot - p.sl_buffer_atr * atr
            risk = entry - sl
        else:
            sl = ob.top + p.sl_buffer_atr * atr
            risk = sl - entry
        if risk <= 0 or entry <= 0:
            return None
        if risk / entry * 100 > p.max_risk_pct:
            return None

        tp, kind = None, "R"
        if p.use_liquidity_tp:
            if side == "LONG":
                for lv in sorted(x for x in self.piv_highs if x > entry):
                    if (lv - entry) / risk >= p.min_rr:
                        tp, kind = lv, "LIQ"
                        break
            else:
                for lv in sorted((x for x in self.piv_lows if x < entry), reverse=True):
                    if (entry - lv) / risk >= p.min_rr:
                        tp, kind = lv, "LIQ"
                        break
        if tp is None:
            tp = entry + risk * p.rr_target if side == "LONG" else entry - risk * p.rr_target
        rr = abs(tp - entry) / risk
        return Signal(side, entry, sl, tp, rr, kind, atr, ob.top, ob.bot, ob.kind,
                      ob.swept, c.t, p.entry_mode)
