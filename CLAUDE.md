# Instrucciones para la pasada diaria

Este repositorio lo ejecuta un agente programado una vez al día. Rutina exacta:

## 1. Comprobar credenciales

Deben existir `AMADEUS_CLIENT_ID` y `AMADEUS_CLIENT_SECRET` como variables de
entorno (secretos del repositorio). Si faltan, **para y avisa** — no sigas.

## 2. Ejecutar la pasada

```bash
python run.py scan
```

Si falla por red o por la API, reintenta **una sola vez**. Si vuelve a fallar,
genera igualmente el informe con el histórico que haya y di en el resumen que la
pasada de hoy no se completó. No inventes precios ni rellenes huecos.

## 3. Generar el informe

```bash
python run.py report --fragment --hours 26
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
- No subas el `.env` ni imprimas las claves en los logs.
- Si una alerta parece un error de tarifa real, repórtala tal cual: es Julio
  quien verifica el precio en la web de la compañía antes de comprar.

## Resumen para Julio

Termina siempre con dos o tres líneas: cuántos precios se guardaron, cuántas
alertas salieron y cuáles, y el enlace al informe.
