"""Reglas de clasificación: distancia a Sol y número de dormitorios.

Ninguna fuente da el número de dormitorios de forma fiable:
- Expedia acepta `min_bed_rooms` pero devuelve igualmente hoteles y residencias.
- Booking no lo incluye ni en la búsqueda ni en las tarifas.
Así que se deduce del nombre de la vivienda/habitación, de la descripción que
lee el agente y de la superficie, y se dice siempre con qué grado de certeza.
"""

from __future__ import annotations

import math
import re
import unicodedata

from .config import SOL

CONFIRMADO = "confirmado"      # consta que tiene >= dormitorios_min
PROBABLE = "probable"          # apartamento grande, sin dato explícito
SIN_VERIFICAR = "sin verificar"
DESCARTADO = "descartado"

# Señales de que NO es un piso entero de 2+ dormitorios.
_NO = [
    (r"\bhotel\b|\bhostal\b|\bhostel\b|\bibis\b|easyhotel|\bnh\b|\bexe\b|\bleonardo\b",
     "es un hotel u hostal"),
    (r"\bresidencia\b|\bstudent|\bestudiantes\b|\bcolegio mayor\b", "es una residencia"),
    (r"\brooms?\b|\bhabitacion(es)?\b|\bcama en\b|\bdormitorio compartido\b|\bshared\b",
     "se alquilan habitaciones, no un piso entero"),
    (r"\bestudio\b|\bstudio\b|\bloft\b", "es un estudio (sin dormitorios separados)"),
    (r"\b(1|un|uno|one)[ -]?(dormitorio|habitacion|bedroom|bed room|hab)\b|\b1 ?br\b",
     "tiene 1 dormitorio"),
]
# Nombres que hablan de otro sitio (playa, costa, otra ciudad) con coordenadas
# en Madrid: anuncio erróneo o directamente fraudulento. Se comprueba ANTES que
# el "2BR" del nombre, que si no lo daría por bueno. Caso real 24/11/2026:
# "Oceanview Paradise 2BR ... Walk to Beach ... Costa Adeje" en Moncloa.
_SOSPECHA = re.compile(
    r"\b(beach|playa|ocean|oceanview|sea ?view|seafront|costa|tenerife|adeje|"
    r"marbella|benidorm|barcelona|valencia|malaga|ibiza|mallorca)\b"
)
MOTIVO_SOSPECHA = "el nombre habla de otro sitio: anuncio erróneo o sospechoso"

# Señales explícitas de 2 o más dormitorios.
_SI = re.compile(
    r"\b([2-9]|dos|tres|cuatro|two|three|four)[ -]?(dormitorios|habitaciones|bedrooms?|"
    r"bed rooms|hab\.?|br)\b"
)
_NUM = {"dos": 2, "tres": 3, "cuatro": 4, "two": 2, "three": 3, "four": 4}


def normaliza(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto or "")
    sin_tildes = "".join(c for c in sin_tildes if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sin_tildes.lower()).strip()


def km_desde_sol(lat: float | None, lon: float | None) -> float | None:
    if lat is None or lon is None:
        return None
    r = 6371.0
    p1, p2 = math.radians(SOL[0]), math.radians(lat)
    dp = p2 - p1
    dl = math.radians(lon - SOL[1])
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(a)), 2)


def dormitorios_en_texto(texto: str) -> int | None:
    """Número de dormitorios si el texto lo dice explícitamente (>= 2)."""
    m = _SI.search(normaliza(texto))
    if not m:
        return None
    n = m.group(1)
    return int(n) if n.isdigit() else _NUM[n]


def motivo_descarte(texto: str) -> str | None:
    t = normaliza(texto)
    if _SOSPECHA.search(t):
        return MOTIVO_SOSPECHA
    # Un "2 dormitorios" explícito gana a palabras sueltas como "rooms" en el nombre.
    if dormitorios_en_texto(t):
        return None
    for patron, motivo in _NO:
        if re.search(patron, t):
            return motivo
    return None


def clasifica_por_nombre(nombre: str, tipo: str | None = None) -> tuple[str, str, int | None]:
    """(estado, motivo, dormitorios) usando sólo el nombre y el tipo de alojamiento."""
    texto = f"{nombre} {tipo or ''}"
    if _SOSPECHA.search(normaliza(texto)):
        return DESCARTADO, MOTIVO_SOSPECHA, None
    n = dormitorios_en_texto(texto)
    if n is not None:
        return CONFIRMADO, f"el anuncio indica {n} dormitorios", n
    motivo = motivo_descarte(texto)
    if motivo:
        return DESCARTADO, motivo, None
    if tipo and re.search(r"hotel|hostal|hostel|albergue", normaliza(tipo)) \
            and not re.search(r"apart", normaliza(tipo)):
        return DESCARTADO, "es un hotel u hostal", None
    return SIN_VERIFICAR, "el anuncio no dice cuántos dormitorios tiene", None


def ocupacion_de_bloque(bloque: str | None) -> int | None:
    """Ocupación de una tarifa de Booking a partir de su `highlighted_blocks`.

    El identificador tiene la forma <habitación>_<tarifa>_<personas>_<…>_<…>.
    Booking devuelve varias tarifas por vivienda, una por número de ocupantes,
    y la más barata suele ser la de 2 personas: usarla daría un precio que no
    es el de 3. Un 0 significa tarifa plana para la unidad (no depende de
    cuántos sean), así que se trata igual que "desconocida": None.
    """
    if not bloque:
        return None
    m = re.search(r"highlighted_blocks=([0-9_]+)", bloque)
    partes = (m.group(1) if m else bloque).split("_")
    if len(partes) >= 3 and partes[2].isdigit() and int(partes[2]) > 0:
        return int(partes[2])
    return None
