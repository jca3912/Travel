# Instrucciones para la pasada diaria

Este repositorio lo ejecuta un agente programado una vez al día. Rutina exacta:

## 1. Preparar el entorno

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

En Windows el intérprete es `.venv\Scripts\python.exe`. El entorno virtual no es
opcional: `fast-flights` necesita protobuf 6+ y choca con lo que suele haber
instalado en el Python del sistema.

La fuente por defecto (`gflights`) **no necesita credenciales**. Sólo si
`config.toml` tiene `provider = "serpapi"` hace falta `SERPAPI_KEY` como
variable de entorno; si en ese caso falta, **para y avisa**.

## 2. Ejecutar la pasada

```bash
.venv/bin/python run.py scan
```

Si falla por red, reintenta **una sola vez**. Si vuelve a fallar, genera
igualmente el informe con el histórico que haya y di en el resumen que la pasada
de hoy no se completó. No inventes precios ni rellenes huecos.

Que fallen unas pocas búsquedas es normal: hay combinaciones de ruta y fecha sin
resultados. Si la pasada termina con estado `degradado`, significa que falló más
del 30 % y que la fuente puede estar rota o bloqueada. En ese caso **dilo de
forma destacada en el resumen**: un informe sin alertas porque la fuente está
caída se parece demasiado a un informe sin alertas porque no hay chollos.

Cuando la pasada corre en la nube, la causa más probable de un `degradado`
persistente es que Google Flights esté bloqueando la IP del datacenter, no que
el paquete se haya roto. Se distingue mirando si falla *todo* desde el primer
día (bloqueo) o si empezó a fallar de golpe tras funcionar semanas (cambio en
la web de Google).

Si `gflights` falla del todo varios días seguidos, la salida es cambiar a
`provider = "serpapi"` en `config.toml`, pero eso cuesta dinero: propónselo a
Julio, no lo hagas por tu cuenta.

## 3. Generar el informe

```bash
.venv/bin/python run.py report --fragment --hours 26
```

Escribe `reports/artifact.html`.

## 4. Publicar

Publica `reports/artifact.html` con la herramienta Artifact pasando **esta URL**
en el parámetro `url`:

```
https://claude.ai/code/artifact/0ce2f5f8-dea4-4d46-ba6a-cdb234b410f2
```

Sin ese parámetro se crearía un artifact nuevo y Julio perdería el enlace que
tiene guardado. Favicon: ✈️ (mantenlo estable).

## 5. Guardar el histórico

```bash
git add data/prices.db
git commit -m "Pasada diaria: <N> precios, <M> alertas"
git push
```

**Este paso no es opcional.** Si el commit no se hace, el histórico del día se
pierde y la línea base nunca se calibra.

## Qué NO hacer

- No toques el umbral `alert_below` de una ruta por tu cuenta. Si crees que está
  mal calibrado, dilo en el resumen y que decida Julio.
- No compres nada, no reserves nada, no entres en webs de aerolíneas.
- No subas el `.env` ni imprimas claves en los logs.
- Si una alerta parece un error de tarifa real, repórtala tal cual: es Julio
  quien verifica el precio en la web de la compañía antes de comprar.

## Resumen para Julio

Termina siempre con dos o tres líneas: cuántos precios se guardaron, cuántas
alertas salieron y cuáles, y el enlace al informe.
