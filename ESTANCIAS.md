# Pasada diaria: piso en Madrid (verano 2027)

Rutina del agente programado que busca apartamento. **No es la pasada de vuelos**:
si tu tarea es esta, ignora los pasos de `CLAUDE.md` sobre `run.py`.

Criterios (en `pisos.toml`): Julio + hija de 15 (con dormitorio propio) + hijo de 11;
2 dormitorios; 18–28 noches entre el 1 jun y el 1 ago 2027; **3.000 $ todo incluido**;
hasta ~30 min de Sol.

No hace falta entorno virtual: `pisos` sólo usa la biblioteca estándar.

## 1. Plan del día

```bash
python3 run_pisos.py plan
```

Devuelve 4 ventanas de fechas con los argumentos **exactos** de cada herramienta.
Cópialos tal cual: no cambies fechas, huéspedes ni filtros.

## 2. Buscar (2 llamadas por ventana)

Para cada ventana, llama a `mcp__Booking_com__accommodations_search_headless` y a
`mcp__Expedia__search_hotels` con sus `args`. Expedia incluye el inventario de Vrbo.

Escribe `data/pisos/<run_date>.json` con este formato. **Transcribe todos los
resultados**, también los que parezcan no cumplir: el filtro lo hace el código y deja
constancia de qué se descartó y por qué.

```json
{
  "run_date": "2026-11-25",
  "busquedas": [
    {"source": "booking", "checkin": "2027-06-01", "checkout": "2027-06-22",
     "ok": true, "error": null, "resultados": [ ... ]},
    {"source": "expedia", "checkin": "2027-06-01", "checkout": "2027-06-22",
     "ok": false, "error": "texto del error de la herramienta", "resultados": []}
  ],
  "verificaciones": []
}
```

Correspondencia de campos de cada resultado:

| Campo | Booking (`accommodations[]`) | Expedia (`data[]`) |
|---|---|---|
| `id` | `id` (como texto) | `hotel_id` |
| `nombre` | `name` | `hotel_name` |
| `url` | `url` **exacta** | `url` **exacta** |
| `total_usd` | `price.book` (comprueba `price.currency` = USD) | `total_price` (comprueba `currency` = USD) |
| `lat` / `lon` | `location.coordinates.latitude/longitude` | `geo_location.latitude/longitude` como número |
| `tipo` | `accommodationType` | `null` |
| `distrito` | `location.district_name` o `null` | `null` |
| `nota` | `rating.review_score` | `guest_rating` como número, o `null` |
| `opiniones` | `rating.number_of_reviews` | `guest_review_count` |

Si una herramienta da error o no responde, reinténtalo **una vez**. Si vuelve a fallar,
pon `"ok": false` con el mensaje. **Nunca inventes ni completes un resultado.**

## 3. Cargar las búsquedas

```bash
python3 run_pisos.py ingest data/pisos/<run_date>.json
```

Si dice `RECHAZADO`, el mensaje indica qué campo no cuadra (normalmente las fechas
del enlace o un precio mal copiado). Corrige la transcripción contra la salida de la
herramienta y vuelve a cargar. No edites el código para que pase.

## 4. Verificar los candidatos (el mismo día)

```bash
python3 run_pisos.py verificar
```

Devuelve hasta 6 viviendas de Booking sin verificar que podrían entrar en presupuesto:

1. Una llamada a `detalles` (todas a la vez). En la descripción busca **sólo** si dice
   explícitamente cuántos dormitorios tiene y si aparece un número de registro o
   licencia turística (VT-…, VUT…, ESFCTU…).
2. Una llamada a `tarifas` por vivienda (`accommodations_room_search`).

Añade a `verificaciones` una entrada por vivienda y **vuelve a cargar el mismo
fichero** con `ingest` (sustituye la pasada del día, no la duplica):

```json
{"id": "1796380", "checkin": "2027-06-07", "checkout": "2027-06-28",
 "dormitorios": null, "evidencia": null, "licencia": null,
 "habitaciones": [
   {"nombre": "Apartamento Ático", "m2": 69, "ocupacion_max": 4, "sofa_cama": true,
    "tarifas": [{"precio_total": 3055.81, "moneda": "USD",
                 "cancelacion": "non_refundable", "url": "<checkout_url exacta>"}]}
 ]}
```

- `dormitorios`: un número sólo si la descripción lo dice. En ese caso `evidencia` es
  la frase literal. Si no lo dice, `null`. No lo deduzcas tú: de eso se encarga el código.
- `sofa_cama`: `true` si "Sofá cama" figura en las `facilities` de esa habitación.
- `tarifas`: **todas** las de cada habitación, con su `checkout_url` exacta. Booking
  da una tarifa por número de ocupantes (el `_3_` de `highlighted_blocks` es para 3
  personas). El código elige la buena; no filtres tú.

## 5. Informe y aviso

```bash
python3 run_pisos.py report --fragment
python3 run_pisos.py resumen
```

Publica `reports/pisos-artifact.html` con la herramienta Artifact pasando **esta URL**
en el parámetro `url` (si no, se crea un enlace nuevo y Julio pierde el suyo):

```
https://claude.ai/artifact/EpN71VajYXreXUj9MCmWxJ
```

Si `resumen` **no** imprime `SIN_NOVEDADES`, envía el texto por email con
`mcp__Gmail__send_message` a la propia dirección de la cuenta de Gmail conectada
(la de Julio). El asunto es la línea `ASUNTO:`; el cuerpo es el resto. No envíes nada
cuando salga `SIN_NOVEDADES`.

## 6. Guardar el histórico

```bash
git add data/pisos.db data/pisos/
git commit -m "Pisos: <N> resultados, <M> novedades"
git push origin main
```

**Obligatorio.** Sin esto, la pasada de mañana no sabe qué es nuevo ni qué ha bajado.

## Qué NO hacer

- No reserves, no inicies pagos y no contactes con anfitriones.
- No cambies `pisos.toml` (tope, zona, fechas) por tu cuenta. Si crees que algo está
  mal calibrado (por ejemplo, semanas sin una sola opción), dilo en el resumen.
- No uses scrapers de Airbnb. El informe ya trae enlaces para mirarlo a mano.
- Si más del 30 % de las búsquedas fallan, `ingest` dirá `DEGRADADO`: **dilo de
  forma destacada**. Un informe vacío por fallo de la fuente parece un informe vacío
  porque no hay pisos.

## Resumen final

Dos o tres líneas: cuántos resultados, cuántas verificaciones, qué novedades (nombre,
fechas, precio y si el precio está verificado para 3), y el enlace al informe.
