# SMC Notebook Bot (BingX · Telegram · Railway)

Port 1:1 del indicador Pine v6 **SMC Notebook · Barrida → MSS → CHOCH → OB · 5m**.
Escanea **todos los perpetuos USDT de BingX** (filtrados por volumen 24h), envía señales a Telegram y, opcionalmente, opera.

## Lógica
Swings (pivotes) → barridas de liquidez → MSS/BOS → CHOCH → Order Block (última vela contraria) → entrada en retest del OB
(`Retest OB` o `Mitad del OB`) → SL bajo/sobre el OB + ATR·colchón → TP en liquidez opuesta o R fijo. Todo sobre **velas cerradas**.

Diferencias respecto al Pine (todas configurables, neutras por defecto salvo las dos primeras):
- TP de liquidez: el Pine usa el último pivote, que a menudo queda del lado equivocado del precio. El bot usa el **pivote más cercano a favor con R:R ≥ `MIN_RR`**; si no hay, usa `RR_TARGET`.
- `MAX_RISK_PCT`: descarta señales con SL demasiado lejos.
- `REQUIRE_SWEEP` (off): el Pine dibuja las barridas pero **no las exige** para entrar; con `true` sí.
- `OB_MIN_AGE` (0): el Pine puede entrar en la misma vela que crea el OB; sube el valor para evitarlo.

## Despliegue
1. Telegram: crea el bot con @BotFather → `TELEGRAM_BOT_TOKEN`. Envíale un mensaje y obtén tu `TELEGRAM_CHAT_ID` (p. ej. con @userinfobot).
2. BingX → API: crea una clave con **Lectura + Trading de Futuros**, **sin retiros**, y limita por IP si puedes.
3. Sube la carpeta a un repo de GitHub.
4. Railway → New Project → Deploy from GitHub repo → añade las variables de `.env.example` (mínimo: `TELEGRAM_*`; para operar: `BINGX_*` y `AUTO_TRADE=true`).
5. Servicio tipo **worker** (sin puerto). Arranca con `python -m smcbot.main` (ya en `railway.json` / `Procfile`).

**Empieza con `AUTO_TRADE=false`**, comprueba las señales unos días y luego activa.

## Ejecución de órdenes
Entrada a mercado + `STOP_MARKET` (SL) + `TAKE_PROFIT_MARKET` (TP), margen aislado, apalancamiento `LEVERAGE`.
Tamaño = `RISK_PCT`% del equity / distancia al SL, limitado por `MAX_MARGIN_PCT`. Máx. `MAX_POSITIONS` simultáneas, una por símbolo.
Si el SL es rechazado, la posición se **cierra** automáticamente. Detecta modo hedge/one-way solo.

## Rendimiento
~500 símbolos: calentamiento inicial ≈ 1–2 min; luego un scan por cierre de vela (+`SCAN_DELAY_SEC`) con 1 petición por símbolo.
Ajusta `REQ_PER_SEC` si BingX devuelve límites de tasa.

## Pruebas locales
```
pip install -r requirements.txt
python -m tests.test_strategy
python -m tests.test_app
cp .env.example .env   # y exporta las variables
python -m smcbot.main
```

> Trading con apalancamiento = riesgo de pérdida total. Sin garantías; úsalo bajo tu responsabilidad.
