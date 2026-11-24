"""pisos — vigilancia de apartamentos para una estancia larga (Madrid, verano 2027).

Los datos no salen de este código: los trae el agente programado a través de
los conectores de Booking.com y Expedia (que incluye Vrbo) y los vuelca en un
JSON normalizado. Este paquete valida ese JSON, lo guarda con histórico,
detecta novedades y bajadas, y genera el informe. Ver ESTANCIAS.md.
"""
