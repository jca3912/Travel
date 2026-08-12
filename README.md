# errorfare

Vigila **Las Vegas → Madrid** y **Los Ángeles → Madrid**, guarda un histórico y
avisa cuando un precio se sale de lo normal. Genera un informe HTML diario.

Sin dependencias: sólo la biblioteca estándar de Python 3.11+.

---

## 1. Credenciales (una sola vez)

1. Regístrate gratis en <https://developers.amadeus.com>.
2. En *My Self-Service Workspace* → **Create New App**. Te da una **API Key** y
   un **API Secret**.
3. Copia `.env.example` a `.env` y pégalos:

```
AMADEUS_CLIENT_ID=tu_api_key
AMADEUS_CLIENT_SECRET=tu_api_secret
```

El `.env` no se sube a ningún sitio y está en el `.gitignore`.

### test vs production

Amadeus te da dos entornos. El de **test** es el que funciona nada más
registrarte, pero sirve datos cacheados y parciales: vale para comprobar que
todo va, no para cazar tarifas. El de **production** tiene los precios reales y
requiere pulsar *Move to Production* en el portal (gratis, tarda 1-2 días en
aprobarse; a partir de 2.000 llamadas/mes empieza a cobrar por llamada).

Cambia `environment = "production"` en `config.toml` cuando te lo aprueben.

---

## 2. Configuración

Todo se toca en `config.toml`. Lo importante:

| Ajuste | Qué hace |
|---|---|
| `[[routes]]` | Un bloque por origen. `alert_below` es el precio que ya te parece chollo. |
| `samples_per_route` | Fechas de salida que se prueban por ruta y pasada. Llamadas ≈ rutas × esto. |
| `trip_lengths` | Duraciones en noches. Cada día rota a la siguiente. |
| `max_stops` | Escalas máximas **por trayecto**. Lo que pase de ahí se descarta. |
| `preferred_max_stops` | Las que quieres de verdad. Ver abajo. |
| `stop_penalty_pct` | Cuánto más barato tiene que ser un itinerario con escalas de más. |
| `max_api_calls_per_run` | Freno de mano para no fundir la cuota. |
| `min_observations` | Precios necesarios antes de fiarse de la estadística. |

### Escalas

Se cuentan **por trayecto**: `max_stops = 2` significa como mucho dos escalas de
ida y dos de vuelta, no dos en todo el viaje. En el informe verás `1+2`, que es
una escala a la ida y dos a la vuelta.

La preferencia no es un filtro duro. Un itinerario que se pasa de
`preferred_max_stops` compite penalizado un `stop_penalty_pct`, así que una
segunda escala sólo se elige cuando ahorra de verdad. Con los valores actuales,
un vuelo de 2 escalas tiene que ser más de un 15 % más barato para ganarle a uno
de 1 escala.

**Cuidado con la cuota**: 2.000 llamadas/mes gratis ≈ 66/día. Con 2 rutas × 16
fechas gastas 32 al día (960/mes), que deja margen para añadir una segunda
pasada diaria. Comprueba siempre la proyección antes de subir nada:

```bash
python run.py scan --dry-run
```

---

## 3. Uso diario

```bash
python run.py daily --open
```

`daily` = consulta la API, guarda los precios, evalúa anomalías y escribe el
informe en `reports/`. Otros comandos:

| Comando | Para qué |
|---|---|
| `python run.py scan` | Sólo consulta y guarda. |
| `python run.py scan --dry-run` | Enseña el plan y el gasto sin llamar a la API. |
| `python run.py report --open` | Regenera el informe y lo abre en el navegador. |
| `python run.py report --fragment` | Versión para publicar como Artifact. |
| `python run.py status` | Estado: observaciones, cuota, calibración por ruta. |
| `python run.py simulate` | Histórico sintético para probarlo todo sin API. |
| `python run.py reset --yes` | Borra la base de datos. |

---

## 4. Cómo decide que algo es un fallo

Dos mecanismos en paralelo. Basta con que salte uno.

**Umbral fijo.** Si el precio baja de `alert_below`, es un CHOLLO. Si baja de la
mitad, es un POSIBLE ERROR. Funciona desde el primer día.

El umbral de Los Ángeles (450 $) es más exigente que el de Las Vegas (550 $) a
propósito: una oferta desde LAX sólo compensa si es bastante más barata, porque
primero hay que plantarse en Los Ángeles.

**Estadística robusta.** Se compara contra el histórico de esa ruta, preferiendo
los vuelos que salen el mismo mes (agosto no vale lo mismo que febrero). Se usa
**mediana + MAD** en vez de media + desviación típica porque los precios de
vuelos tienen colas largas: una tarifa rara colada en el histórico destroza una
media, pero no mueve la mediana.

```
z = 0.6745 × (mediana − precio) / MAD
```

- `z ≥ 3` o caída ≥ 35 % → CHOLLO
- `z ≥ 5` o caída ≥ 60 % → POSIBLE ERROR

Necesita `min_observations` precios (8 por defecto) para activarse. Antes de
eso sólo actúa el umbral fijo. Si el histórico es degenerado (todos los precios
casi idénticos) el MAD se ignora y se decide sólo por caída porcentual, porque
si no cualquier variación dispararía un z-score absurdo.

Una alerta repetida para la misma ruta y fechas se silencia durante
`cooldown_hours`.

---

## 5. Base de datos

SQLite en `data/prices.db`. Tres tablas: `observations` (un precio guardado por
búsqueda, siempre el más barato), `alerts` y `runs`. Es un fichero normal:
cópialo para hacer backup. **Es el activo del proyecto** — el sistema no vale
nada sin histórico acumulado, así que no lo borres a la ligera.

---

## 6. Aviso

Una alerta no es una reserva. Las tarifas erróneas se corrigen en minutos y la
aerolínea puede cancelar el billete incluso después de emitirlo. Verifica
siempre el precio en la web de la compañía antes de comprar.
