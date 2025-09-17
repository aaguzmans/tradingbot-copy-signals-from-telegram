# XAU Copy Signal Bot v5.1 – Documentación Funcional

## 1. Descripción General

Este bot automatiza la copia de señales de trading de un canal de Telegram hacia MetaTrader 5, **colocando órdenes pendientes de compra o venta** según los mensajes recibidos.  

### Funcionalidades Principales

- Lectura de mensajes nuevos desde un canal de Telegram.
- Extracción de parámetros de trading: tipo de operación, precio o rango de entrada, SL y TP.
- Cálculo de precio de orden pendiente según **estrategia configurada (`entry_strategy`)** y **zona central (`central_zone`)**.
- Colocación automática de órdenes pendientes en MT5.
- Actualización dinámica de Stop Loss si se detecta mensaje correspondiente.
- Limpieza periódica de órdenes expiradas o activadas.
- Logging completo de eventos y operaciones.

---

## 2. Configuración

El bot utiliza un archivo `config_v2.json` con la siguiente estructura:
Crea un duplicado de `config_v2_test.json` y renómbralo a `config_v2.json`.
Luego coloca todas tus credenciales necesarias.

```json
{
  "telegram": {
    "api_id": 11111111,
    "api_hash": "zz894lam297dma357bd8f2cfd4ade1a8",
    "phone": "+50688888888",
    "channel_username": "telegram_channel_test"
  },
  "mt5": {
    "login": 111111111,
    "password": "ZFhnfzki*",
    "server": "Exness-MT5Trial11"
  },
  "trading": {
    "symbol": "XAUUSD",
    "volume": 0.01,
    "target_profit_usd": 5.00,
    "use_minimum_volume": true,
    "max_sl_distance_pips": 110,
    "entry_strategy": "min",
    "central_zone": 0
  }
}

```
### Parámetros Clave de Trading

| Parámetro           | Función                                                                 |
|--------------------|-------------------------------------------------------------------------|
| `symbol`            | Símbolo de trading (ej. XAUUSD).                                        |
| `volume`            | Tamaño de la operación.                                                 |
| `target_profit_usd` | Ganancia deseada en USD para calcular automáticamente el TP.            |
| `entry_strategy`    | Estrategia de entrada (`auto`, `min`, `max`) para calcular precio de orden pendiente dentro del rango. |
| `central_zone`      | Desplazamiento adicional para acercar la entrada al centro de la zona de rango. |


## 3. Procesamiento de Mensajes

El bot interpreta dos tipos de mensajes:

### 3.1. Señales de orden pendiente

Ejemplo de mensaje:

```json

Sell Gold @3640.5-3645.5
Sl :3647.5
Tp1 :3638.5
Tp2 :3636
Enter Slowly-Layer with proper money management ..

```

### Pasos funcionales del bot:

**1. Detecta el tipo de operación (`buy` o `sell`) mediante patrones predefinidos.**

**2. Extrae el rango de entrada (`3640.5-3645.5`) o precio único si se especifica.**

**3. Extrae Stop Loss (SL) y Take Profit (TP).**

**4. Calcula el precio de entrada pendiente utilizando:**

  - `entry_strategy`:

    - `min`: Para buy → mínimo del rango; para sell → máximo del rango.

    - `max`: Para buy → máximo; para sell → mínimo.

    - `auto`: Buy usa mínimo, Sell usa máximo.

  - `central_zone`: Ajuste adicional del precio calculado.

**5. Coloca la orden pendiente en MT5 con:**

  - Tipo de orden (`LIMIT` o `STOP`) según relación precio pendiente vs precio actual.

  - Volumen calculado según `volume` o `use_minimum_volume`.

  - Stop Loss (`sl`) y Take Profit calculados para `target_profit_usd`.

### 3.2. Ejemplos de cálculo de precio pendiente

| Tipo | Rango Entrada   | Estrategia | Central Zone | Precio Pendiente Calculado |
|------|----------------|------------|--------------|----------------------------|
| Sell | 3640.5-3645.5  | min        | 0            | 3645.5 (máx del rango)     |
| Sell | 3640.5-3645.5  | min        | 1            | 3644.5 (ajustado 1 pip)    |
| Buy  | 3640.5-3645.5  | max        | 0            | 3645.5 (máx del rango)     |
| Buy  | 3640.5-3645.5  | max        | -0.5         | 3645.0 (ajustado -0.5)     |
| Buy  | 3640.5-3645.5  | auto       | 0            | 3640.5 (mín del rango)     |
| Sell | 3640.5-3645.5  | auto       | 0            | 3645.5 (máx del rango)     |


✅ Nota: central_zone permite acercar la entrada al centro del rango, ajustando según la estrategia.

## 3.3. Mensajes de actualización de Stop Loss

Ejemplo:
```json

I'll move my SL to 3383 temporarily...

```

### Funcionalidad:

- Detecta el nuevo valor de SL (3383) mediante expresiones regulares.

- Actualiza automáticamente:

    - Posiciones activas en MT5.

    - Órdenes pendientes si no hay posiciones activas.

- Logea el resultado de la actualización:

```json

✅ SL actualizado en posición activa 123456 -> 3383

```

## 4. Ejemplo de Flujo Completo

**1.Bot recibe mensaje de señal de venta:**

```json

Sell Gold @3640.5-3645.5
Sl :3647.5
Tp1 :3638.5

```

**2.El bot procesa mensaje:**

  - Tipo: Sell

  - Rango: 3640.5-3645.5

  - SL: 3647.5

  - TP calculado según `target_profit_usd`.

**3.Calcula precio pendiente con `entry_strategy = min` y `central_zone = 0` → 3645.5.**

**4.Coloca orden pendiente SELL LIMIT en MT5 con:**

  - Entrada: 3645.5

  - SL: 3647.5

  - TP: calculado para $5 de ganancia

**5.Monitoriza órdenes pendientes y posiciones.**

**6.Si llega mensaje "I'll move my SL to 3383...", actualiza SL en tiempo real.**

## 5. Consideraciones

- Si el mensaje no contiene parámetros válidos, el bot **ignora** la señal.

- Si el precio pendiente calculado está demasiado lejos del precio actual (definido por `max_sl_distance_pips`), se puede ajustar según la configuración.

- `target_profit_usd` asegura que el TP sea coherente con la ganancia deseada y volumen de la operación.

## 6. Logging

- Todos los eventos se registran en `trading_bot_v5_pending.log`.

- Incluye:

  - Mensajes recibidos.

  - Parámetros extraídos.

  - Ordenes colocadas y modificadas.

  - Errores y advertencias.

## 7. Resumen Visual

```json

Mensaje recibido: Sell Gold @3640.5-3645.5
SL: 3647.5 | TP calculado: 3638.7

-> Calcular precio pendiente usando 'entry_strategy' = min y 'central_zone' = 0
-> Precio pendiente calculado = 3645.5
-> Tipo orden: SELL LIMIT
-> Volumen: 0.01
-> Orden enviada a MT5

Si llega mensaje: "I'll move my SL to 3353 temporarily..."
-> SL actualizado automáticamente en MT5

```