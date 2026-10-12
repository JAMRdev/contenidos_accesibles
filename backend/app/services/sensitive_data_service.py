"""
Detección local de datos personales sensibles (Issue #66).

Corre ANTES de cualquier llamada a la IA y antes de guardar el material en la base:
si el texto contiene datos personales, el análisis se corta y el docente recibe
la lista de hallazgos para quitarlos y volver a cargar el material.

Es 100% local (expresiones regulares, sin IA ni tokens para que se evalúe antes de que llegue a la IA).
Contempla Nombre y apellido del "estudiante" o "alumno" o "paciente", DNI CUIL y CUIT, y "calificaciones" o "notas".
"""

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass

# Cantidad máxima de hallazgos que se devuelven al cliente (el total se informa aparte).
MAX_REPORTED_FINDINGS = 20

SENSITIVE_DATA_MESSAGE = (
    "El material contiene posibles datos personales (por ejemplo, nombres de estudiantes, "
    "DNI o calificaciones). Por privacidad no se envió a la IA ni se guardó. "
    "Quitá esos datos y volvé a cargar el material, o descartá los hallazgos que no sean datos personales."
)

TYPE_LABELS = {
    "dni": "DNI",
    "cuil": "CUIL/CUIT",
    "nombre_rotulado": "Nombre de una persona",
    "calificacion": "Calificación",
}

# --- Patrones -----------------------------------------------------------------
# Cada patrón captura el dato en el grupo con nombre 'value' (lo que se enmascara).

# 7 u 8 cifras, con o sin puntos: 12345678 / 12.345.678 / 1.234.567
_DNI_NUMBER = r"\d{1,2}\.?\d{3}\.?\d{3}"

_DNI_RE = re.compile(
    r"(?i:\bD\.?[ \t]?N\.?[ \t]?I\b\.?|\bdocumento(?:[ \t]+nacional)?(?:[ \t]+de[ \t]+identidad)?\b)"
    r"[ \t]*(?i:n[°ºo]\.?|nro\.?|n[uú]mero)?[ \t]*[:#]?[ \t]*"
    rf"(?P<value>{_DNI_NUMBER})(?![\d.]*\d)"
)

# CUIL/CUIT con guiones (formato inconfundible, no necesita rótulo): 20-12345678-9 / 20-12.345.678-9
_CUIL_FORMATTED_RE = re.compile(
    r"(?<![\d-])(?P<value>(?:20|23|24|27|30|33|34)-(?:\d{8}|\d{2}\.\d{3}\.\d{3})-\d)(?![\d-])"
)

# CUIL/CUIT sin guiones: solo con el rótulo delante.
_CUIL_LABELED_RE = re.compile(
    r"(?i:\bCUI[LT]\b)[ \t]*(?i:n[°ºo]\.?|nro\.?)?[ \t]*[:#]?[ \t]*"
    r"(?P<value>\d{2}-?(?:\d{8}|\d{2}\.\d{3}\.\d{3})-?\d)(?![\d-])"
)

_NAME_LABEL = (
    r"(?i:\b(?:"
    r"alumn[oa](?:/[oa]|\([oa]\))?s?"
    r"|estudiantes?"
    r"|pacientes?|ptes?\.?"
    r"|nombre(?:[ \t]+y[ \t]+apellidos?|[ \t]+completo)?"
    r"|apellidos?(?:[ \t]+y[ \t]+nombres?)?"
    r"))"
)

# Nombre con mayúscula inicial: al menos nombre + apellido, en el mismo renglón.
# Admite partículas ("María de los Ángeles", "Juan del Valle").
_NAME_WORD = r"[A-ZÁÉÍÓÚÑÜ][A-Za-zÁÉÍÓÚÑÜáéíóúñü'\-]+"
_PARTICLE = r"(?:de|del|la|las|los|y)"
_NAME = rf"{_NAME_WORD}(?:(?:[ \t]+{_PARTICLE})*[ \t]+{_NAME_WORD}){{1,3}}"

_LABELED_NAME_RE = re.compile(rf"{_NAME_LABEL}[ \t]*:[ \t]*(?P<value>{_NAME})")

# Nombre en minúscula: más ambiguo, así que debe ocupar el resto del renglón
# (o terminar en una coma, punto y coma, guion o barra) y tener de 2 a 4 palabras
# sin contar las partículas ("maría de los ángeles gómez").
_LOWER_WORD = r"[a-záéíóúñü][a-záéíóúñü'\-]+"
_LOWER_NAME = rf"{_LOWER_WORD}(?:(?:[ \t]+{_PARTICLE})*[ \t]+{_LOWER_WORD}){{1,3}}"
_LOWERCASE_NAME_RE = re.compile(
    rf"{_NAME_LABEL}[ \t]*:[ \t]*(?P<value>{_LOWER_NAME})[ \t]*(?=$|[,;|/\-–]|\.[ \t]*$)",
    re.MULTILINE,
)

# Palabras que no forman parte de un nombre (artículos, preposiciones, verbos frecuentes
# en consignas). Evita marcar "Alumno: debe entregar el trabajo" o "Alumnos: trabajen en grupo".
_NOT_NAME_EDGE = {
    "de", "del", "la", "las", "los", "el", "y", "e", "o", "u", "un", "una", "unos", "unas",
    "con", "en", "para", "por", "que", "se", "su", "sus", "al", "a", "lo", "le", "les",
    "debe", "deben", "deberá", "deberán", "tiene", "tienen", "puede", "pueden", "va", "van",
    "es", "son", "está", "están", "hace", "hacen", "lee", "leen", "escribe", "escriben",
}
# Los nombres que terminan en vocal acentuada son pocos; las consignas en voseo
# ("leé", "completá", "escribí") casi siempre terminan así.
_NAMES_WITH_STRESSED_ENDING = {"josé", "noé", "renée", "andré", "rafaé"}


_PARTICLES = {"de", "del", "la", "las", "los", "y"}


def _looks_like_lowercase_name(value: str) -> bool:
    words = value.split()
    if words[0] in _PARTICLES or words[-1] in _PARTICLES:
        return False
    for word in words:
        if word in _PARTICLES:
            continue
        if word in _NOT_NAME_EDGE:
            return False  # "trabajen en grupo", "debe entregar el trabajo"
        if word[-1] in "áéí" and word not in _NAMES_WITH_STRESSED_ENDING:
            return False  # imperativo en voseo: "leé", "completá", "escribí"
    return True


# Calificación: rótulo + ':' o '=' + valor. "Nota: leé el capítulo 2" no se marca.
# "Puntaje" queda afuera a propósito: en los exámenes indica cuánto vale cada ejercicio.
_GRADE_WORDS = (
    r"aprobad[oa]|desaprobad[oa]|insuficiente|suficiente|satisfactori[oa]"
    r"|no[ \t]+satisfactori[oa]|bueno|muy[ \t]+bueno|distinguido|sobresaliente|excelente"
)
_GRADE_RE = re.compile(
    r"(?i:\b(?:nota|calificaci[oó]n)"
    r"(?:[ \t]+(?:final|obtenida|definitiva|del[ \t]+trimestre|trimestral|cuatrimestral|del[ \t]+parcial|parcial))?)"
    r"[ \t]*[:=][ \t]*"
    rf"(?P<value>100|\d{{1,2}}(?:[.,]\d{{1,2}})?(?![\d.,]*\d)|(?i:{_GRADE_WORDS})\b)"
)

# El orden define la prioridad cuando dos patrones se superponen.
# El tercer elemento es un filtro opcional sobre el valor encontrado.
_PATTERNS: list[tuple[str, re.Pattern, object]] = [
    ("cuil", _CUIL_LABELED_RE, None),
    ("cuil", _CUIL_FORMATTED_RE, None),
    ("dni", _DNI_RE, None),
    ("nombre_rotulado", _LABELED_NAME_RE, None),
    ("nombre_rotulado", _LOWERCASE_NAME_RE, _looks_like_lowercase_name),
    ("calificacion", _GRADE_RE, None),
]


@dataclass(frozen=True)
class SensitiveFinding:
    id: str
    type: str
    label: str
    masked: str
    line: int


class SensitiveDataError(Exception):
    """
    El material contiene datos personales. No hereda de ValueError a propósito:
    los endpoints capturan ValueError como 400 genérico y este caso necesita su propia respuesta.
    """

    def __init__(self, findings: list[SensitiveFinding]):
        self.findings = findings
        super().__init__(SENSITIVE_DATA_MESSAGE)

    def to_detail(self) -> dict:
        """Cuerpo de 'detail' para la respuesta HTTP. Nunca incluye el dato sin enmascarar."""
        return {
            "code": "sensitive_data_detected",
            "message": SENSITIVE_DATA_MESSAGE,
            "total": len(self.findings),
            "findings": [asdict(f) for f in self.findings[:MAX_REPORTED_FINDINGS]],
        }


# --- Enmascarado ----------------------------------------------------------------


def _mask_digits(value: str) -> str:
    """Conserva la primera y la última cifra y los separadores: 12.345.678 -> 1*.***.**8"""
    digit_positions = [i for i, ch in enumerate(value) if ch.isdigit()]
    if len(digit_positions) <= 2:
        return re.sub(r"\d", "*", value)
    keep = {digit_positions[0], digit_positions[-1]}
    return "".join("*" if ch.isdigit() and i not in keep else ch for i, ch in enumerate(value))


def _mask_name(value: str) -> str:
    """Juan Pérez -> J*** P***"""
    return " ".join(f"{word[0]}***" for word in value.split())


def _mask(finding_type: str, value: str) -> str:
    if finding_type in ("dni", "cuil"):
        return _mask_digits(value)
    if finding_type == "nombre_rotulado":
        return _mask_name(value)
    return "***"  # calificación: el valor completo es el dato sensible


def _masked_match(finding_type: str, match: re.Match) -> str:
    """El texto encontrado (rótulo incluido) con el dato enmascarado: 'DNI: 1*.***.**8'."""
    full = match.group(0)
    start = match.start("value") - match.start()
    end = match.end("value") - match.start()
    return " ".join((full[:start] + _mask(finding_type, match.group("value")) + full[end:]).split())


# --- API del servicio --------------------------------------------------------------


def detect(text: str) -> list[SensitiveFinding]:
    """Devuelve los datos personales encontrados en el texto, enmascarados y en orden de aparición."""
    if not text or not text.strip():
        return []

    taken: list[tuple[int, int]] = []
    found: list[tuple[int, SensitiveFinding]] = []
    for finding_type, pattern, accept in _PATTERNS:
        for match in pattern.finditer(text):
            value = match.group("value")
            if accept is not None and not accept(value):
                continue
            start, end = match.span("value")
            if any(start < t_end and t_start < end for t_start, t_end in taken):
                continue  # ya lo marcó un patrón de mayor prioridad
            taken.append((start, end))
            found.append((
                start,
                SensitiveFinding(
                    # Tipo + posición: estable si se reenvía el mismo texto y no expone el dato.
                    id=f"{finding_type}-{start}",
                    type=finding_type,
                    label=TYPE_LABELS[finding_type],
                    masked=_masked_match(finding_type, match),
                    line=text.count("\n", 0, start) + 1,
                ),
            ))

    return [finding for _, finding in sorted(found, key=lambda item: item[0])]


def ensure_no_sensitive_data(text: str, dismissed: Iterable[str] = ()) -> list[SensitiveFinding]:
    """
    Lanza SensitiveDataError si el texto contiene datos personales no descartados por el docente.

    Devuelve los hallazgos que el docente descartó (lista vacía si no hubo ninguno), para
    que quien llama pueda dejar constancia en los avisos de la respuesta.
    """
    dismissed_ids = set(dismissed)
    findings = detect(text)
    pending = [f for f in findings if f.id not in dismissed_ids]
    if pending:
        raise SensitiveDataError(pending)
    return findings
