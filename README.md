# errorfare

Vigila **Las Vegas → Madrid** y **Los Ángeles → Madrid**, guarda un histórico y
avisa cuando un precio se sale de lo normal. Genera un informe HTML diario.

---

## 1. Instalación

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

El entorno virtual es necesario: `fast-flights` exige protobuf 6+ y eso choca
con otras cosas que puedas tener en el Python del sistema.

A partir de aquí, todos los comandos usan `.venv\Scripts\python.exe` en vez de
`python`.

**Con la configuración por defecto no hacen falta credenciales.**

---

## 2. Fuentes de precios

Se elige con `provider` en `config.toml`.

| | `gflights` (por defecto) | `serpapi` |
|---|---|---|
| Coste | Gratis | 25 $/mes, 1.000 búsquedas |
| Registro | No | Sí, clave en `.env` |
| Datos | Google Flights | Google Flights |
| Estabilidad | Scraper no oficial | Servicio de pago |
| Desde servidor | Puede bloquearse | Sin problema |

`gflights` funciona hoy y da precios reales en ~1,2 s por búsqueda. Su riesgo es
que es un scraper: si Google cambia su web, deja de funcionar hasta que
actualicen el paquete. Por eso la pasada avisa cuando fallan más del 30 % de las
búsquedas, en vez de generar un informe vacío que parecería "no hay chollos".

`serpapi` es el plan B. Cambias una línea en `config.toml`, pones `SERPAPI_KEY`
en el `.env` y listo. **Aviso: ese adaptador está escrito contra la
documentación pero nunca se ha ejecutado contra la API real** — revisa la
primera pasada con `-v` antes de fiarte.

> Nota histórica: este proyecto empezó sobre la API Self-Service de Amadeus.
> Amadeus la cerró el 17 de julio de 2026 y desactivó las claves existentes.
> De ahí que la fuente de datos esté detrás de una interfaz: cambiar de
> proveedor es una línea de configuración, no una reescritura.

---

## 3. Configuración

Todo se toca en `config.toml`.

| Ajuste | Qué hace |
|---|---|
| `provider` | Fuente de precios: `gflights` o `serpapi`. |
| `cabins` | Cabinas a vigilar. Cada una multiplica las búsquedas. |
| `[[routes]]` | Un bloque por origen, con un `alert_below` por cabina. |
| `samples_per_route` | Fechas de salida que se prueban por ruta y pasada. |
| `trip_lengths` | Duraciones en noches. Cada día rota a la siguiente. |
| `max_stops` | Escalas máximas **por trayecto**. |
| `preferred_max_stops` | Las que quieres de verdad. Ver abajo. |
| `stop_penalty_pct` | Cuánto más barato tiene que ser un itinerario con escalas de más. |
| `min_observations` | Precios necesarios antes de fiarse de la estadística. |

### Escalas

Se cuentan **por trayecto**: `max_stops = 2` significa como mucho dos escalas de
ida y dos de vuelta, no dos en todo el viaje. El límite se aplica en la propia
consulta, así que los dos trayectos quedan acotados.

Lo que el informe muestra (escalas y ruta, tipo `LAS-IAD-MAD`) es el trayecto de
**ida**: ni Google Flights ni SerpApi devuelven la vuelta en la primera
respuesta, dan los itinerarios de ida con el precio total del ida y vuelta.

La preferencia no es un filtro duro. Un itinerario que se pasa de
`preferred_max_stops` compite penalizado un `stop_penalty_pct`, así que una
segunda escala sólo se elige cuando ahorra de verdad. Con los valores actuales,
un vuelo de 2 escalas tiene que ser más de un 15 % más barato para ganarle a uno
de 1 escala.

### Cabinas

Cada cabina se busca por separado y mantiene **su propio histórico**. Es
necesario: 900 $ es un martes cualquiera en turista y un error de tarifa en
business, así que una mediana conjunta no describiría ninguna de las dos.

Los umbrales fijos van por cabina:

```toml
[routes.alert_below]
ECONOMY         = 550
PREMIUM_ECONOMY = 950
BUSINESS        = 1900
```

### Consumo

Con 2 rutas × 12 fechas × 3 cabinas son 72 búsquedas al día, ~2.160 al mes. Con
`gflights` es gratis. Con `serpapi` no cabe ni de lejos en el plan de 1.000, así
que habría que recortar cabinas o fechas. Comprueba la proyección antes de subir
nada:

```bash
.venv\Scripts\python.exe run.py scan --dry-run
```

---

## 4. Uso diario

```bash
.venv\Scripts\python.exe run.py daily --open
```

`daily` = consulta, guarda, evalúa anomalías y escribe el informe en `reports/`.

| Comando | Para qué |
|---|---|
| `run.py scan` | Sólo consulta y guarda. |
| `run.py scan --dry-run` | Enseña el plan y el gasto sin consultar nada. |
| `run.py report --open` | Regenera el informe y lo abre en el navegador. |
| `run.py report --fragment` | Versión para publicar como Artifact. |
| `run.py status` | Estado: observaciones, calibración por ruta, salud de la fuente. |
| `run.py simulate` | Histórico sintético para probar sin consultar. |
| `run.py reset --yes` | Borra la base de datos. |

Y los tests de la lógica que no toca la red:

```bash
.venv\Scripts\python.exe tests\test_logic.py
```

---

## 5. Cómo decide que algo es un fallo

Dos mecanismos en paralelo. Basta con que salte uno.

**Umbral fijo.** Si el precio baja del `alert_below` de esa cabina, es un
CHOLLO. Si baja de la mitad, es un POSIBLE ERROR. Funciona desde el primer día.

Los umbrales de Los Ángeles son más exigentes que los de Las Vegas a propósito:
una oferta desde LAX sólo compensa si es bastante más barata, porque primero hay
que plantarse en Los Ángeles.

**Comparación entre cabinas.** Una business que cuesta menos de
`cross_cabin_ratio` veces la mediana de turista se marca como POSIBLE ERROR
directamente. Es la firma de los errores de tarifa más sonados: la cabina buena
publicada al precio de la mala. Tiene una ventaja importante — no espera a que
haya histórico de business, que es justo el que más tarda en acumularse.

Con turista en torno a 800 $ y ratio 1,3, cualquier business por debajo de
~1.040 $ salta como error. Lo normal en esa cabina son 4.800 $.

**Estadística robusta.** Se compara contra el histórico de esa ruta, prefiriendo
los vuelos que salen el mismo mes (agosto no vale lo mismo que febrero). Se usa
**mediana + MAD** en vez de media + desviación típica porque los precios de
vuelos tienen colas largas: una tarifa rara colada en el histórico destroza una
media, pero no mueve la mediana.

```
z = 0.6745 × (mediana − precio) / MAD
```

- `z ≥ 3` **y** caída ≥ 15 %, o caída ≥ 35 % → CHOLLO
- `z ≥ 5` **y** caída ≥ 40 %, o caída ≥ 60 % → POSIBLE ERROR

El z-score dice si el precio es *raro*; la caída porcentual, si *merece la pena*.
Hacen falta las dos, y esto no es teórico: con el histórico de business, que es
muy estable, una bajada del 20 % daba z=8 y se marcaba como error de tarifa. Lo
detectaron los tests.

Necesita `min_observations` precios (8 por defecto) para activarse. Si el
histórico es degenerado (todos los precios casi idénticos) el MAD se ignora y se
decide sólo por caída porcentual.

Una alerta repetida para la misma ruta y fechas se silencia durante
`cooldown_hours`.

### Referencia real

Precios observados el 12/08/2026, ida y vuelta por persona:

| Ruta | Turista | Premium | Business |
|---|---|---|---|
| LAS → MAD | 740 – 1.400 $ | 1.230 – 2.087 $ | 3.950 – 6.383 $ |
| LAX → MAD | 671 – 1.266 $ | 1.130 – 2.270 $ | 3.366 – 5.273 $ |

Casi todo con una escala: Londres, Lisboa, Copenhague, Washington, Miami.

Los umbrales configurados quedan muy por debajo de lo habitual, que es lo que se
busca: sólo saltan ante algo genuinamente anómalo, no ante una semana barata.

---

## 6. Base de datos

SQLite en `data/prices.db`. Tres tablas: `observations` (un precio por búsqueda,
siempre el elegido según la preferencia de escalas), `alerts` y `runs`. Es un
fichero normal: cópialo para hacer backup. **Es el activo del proyecto** — el
sistema no vale nada sin histórico acumulado, así que no lo borres a la ligera.

Si el esquema cambia, al abrir la base se añaden solas las columnas que falten.
Nunca se borra nada.

---

## 7. Aviso

Una alerta no es una reserva. Las tarifas erróneas se corrigen en minutos y la
aerolínea puede cancelar el billete incluso después de emitirlo. Verifica
siempre el precio en la web de la compañía antes de comprar.
