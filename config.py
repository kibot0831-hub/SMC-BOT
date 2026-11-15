"""Configuración por variables de entorno (Railway → Variables)."""
import os
from dataclasses import dataclass

TF_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000,
}


def _s(name, default=""):
    return os.getenv(name, default).strip()


def _f(name, default):
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return float(default)


def _i(name, default):
    try:
        return int(float(os.getenv(name, str(default))))
    except ValueError:
        return int(default)


def _b(name, default):
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "si", "sí", "on")


def norm_symbol(s: str) -> str:
    s = s.strip().upper().replace("/", "-").replace("_", "-")
    if not s:
        return s
    if "-" not in s and s.endswith("USDT"):
        s = s[:-4] + "-USDT"
    return s


def _list(name):
    return {norm_symbol(x) for x in _s(name).split(",") if x.strip()}


@dataclass(frozen=True)
class Config:
    # Credenciales
    bingx_key: str
    bingx_secret: str
    tg_token: str
    tg_chat: str
    # Escaneo
    timeframe: str
    tf_ms: int
    warmup: int
    min_volume: float
    max_symbols: int
    include: frozenset
    exclude: frozenset
    scan_delay: float
    req_per_sec: float
    universe_refresh_min: int
    heartbeat_hours: float
    # Estrategia (idéntica al Pine)
    swing_len: int
    entry_mode: str
    ob_mitigation: str
    cooldown_bars: int
    sl_buffer_atr: float
    rr_target: float
    use_liquidity_tp: bool
    # Filtros extra (opcionales)
    min_rr: float
    max_risk_pct: float
    require_sweep: bool
    sweep_lookback: int
    ob_min_age: int
    # Trading
    auto_trade: bool
    risk_pct: float
    fixed_margin_usdt: float
    leverage: int
    max_margin_pct: float
    max_positions: int
    margin_mode: str


def load() -> Config:
    tf = _s("TIMEFRAME", "5m")
    if tf not in TF_MS:
        raise SystemExit(f"TIMEFRAME inválido: {tf}. Opciones: {list(TF_MS)}")
    entry_mode = _s("ENTRY_MODE", "Retest OB")
    if entry_mode not in ("Retest OB", "Mitad del OB"):
        entry_mode = "Retest OB"
    mit = _s("OB_MITIGATION", "Close through")
    if mit not in ("Close through", "Wick through"):
        mit = "Close through"
    return Config(
        bingx_key=_s("BINGX_API_KEY"),
        bingx_secret=_s("BINGX_API_SECRET"),
        tg_token=_s("TELEGRAM_BOT_TOKEN"),
        tg_chat=_s("TELEGRAM_CHAT_ID"),
        timeframe=tf,
        tf_ms=TF_MS[tf],
        warmup=max(120, _i("WARMUP_BARS", 500)),
        min_volume=_f("MIN_VOLUME_USDT", 1_000_000),
        max_symbols=_i("MAX_SYMBOLS", 0),
        include=frozenset(_list("INCLUDE_SYMBOLS")),
        exclude=frozenset(_list("EXCLUDE_SYMBOLS")),
        scan_delay=_f("SCAN_DELAY_SEC", 4),
        req_per_sec=_f("REQ_PER_SEC", 8),
        universe_refresh_min=_i("UNIVERSE_REFRESH_MIN", 60),
        heartbeat_hours=_f("HEARTBEAT_HOURS", 6),
        swing_len=max(3, min(15, _i("SWING_LEN", 5))),
        entry_mode=entry_mode,
        ob_mitigation=mit,
        cooldown_bars=max(0, _i("COOLDOWN_BARS", 8)),
        sl_buffer_atr=_f("SL_BUFFER_ATR", 0.1),
        rr_target=max(1.0, _f("RR_TARGET", 2.5)),
        use_liquidity_tp=_b("USE_LIQUIDITY_TP", True),
        min_rr=_f("MIN_RR", 1.0),
        max_risk_pct=_f("MAX_RISK_PCT", 3.0),
        require_sweep=_b("REQUIRE_SWEEP", False),
        sweep_lookback=_i("SWEEP_LOOKBACK", 30),
        ob_min_age=_i("OB_MIN_AGE", 0),
        auto_trade=_b("AUTO_TRADE", False),
        risk_pct=_f("RISK_PCT", 1.0),
        fixed_margin_usdt=_f("FIXED_MARGIN_USDT", 0),
        leverage=max(1, _i("LEVERAGE", 5)),
        max_margin_pct=_f("MAX_MARGIN_PCT", 20),
        max_positions=_i("MAX_POSITIONS", 5),
        margin_mode=_s("MARGIN_MODE", "ISOLATED").upper(),
    )
