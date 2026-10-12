"""Tests del detector local de datos personales (Issue #66). No llaman a la IA."""

import re

import pytest

from app.services.sensitive_data_service import (
    MAX_REPORTED_FINDINGS,
    SensitiveDataError,
    detect,
    ensure_no_sensitive_data,
)


def _types(text: str) -> list[str]:
    return [f.type for f in detect(text)]


# --- Datos que SÍ se detectan ---------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "DNI: 12.345.678",
        "DNI 12345678",
        "D.N.I. N° 30.123.456",
        "dni nro 7.654.321",
        "Documento: 40123456",
        "Documento Nacional de Identidad: 35.111.222",
    ],
)
def test_detecta_dni_con_rotulo(text):
    assert _types(text) == ["dni"]


@pytest.mark.parametrize(
    "text",
    ["20-12345678-9", "Contacto: 27-12.345.678-4", "CUIL: 20123456789", "CUIT N° 30-71234567-1"],
)
def test_detecta_cuil_cuit(text):
    assert _types(text) == ["cuil"]


@pytest.mark.parametrize(
    "text",
    [
        "Alumno: Juan Pérez",
        "Alumna: María de los Ángeles Gómez",
        "Alumno/a: Lucas Fernández",
        "Estudiante: Sofía Martínez Ruiz",
        "Paciente: Carlos del Valle",
        "Pte: Carlos Díaz",
        "pte.: Ana Gómez",
        "ESTUDIANTE: Juan Pérez",
        "estudiante: Juan Pérez",
        "ESTUDIANTE: JUAN PÉREZ",
        "Nombre y apellido: Ana López",
        "Apellido y nombre: Torres Julieta",
        "nombre completo: Pedro Ramírez",
    ],
)
def test_detecta_nombres_rotulados(text):
    assert _types(text) == ["nombre_rotulado"]


@pytest.mark.parametrize(
    "text",
    [
        "Estudiante: juan pérez",
        "alumno: maría de los ángeles gómez",
        "Paciente: josé díaz",
        "Alumna: ana lópez, 3er año",
        "Estudiante: lucas fernández - DNI pendiente",
        "Alumno: martín sosa.",
    ],
)
def test_detecta_nombres_rotulados_en_minuscula(text):
    assert _types(text) == ["nombre_rotulado"]


@pytest.mark.parametrize(
    "text",
    [
        "Alumno: completá la tabla",
        "Estudiante: leé el texto.",
        "Alumna: escribí tu respuesta",
        "Alumno: debe entregar el trabajo",
        "Estudiantes: lee el capítulo",
        "Nombre: tarea",
        "Alumnos: trabajen en grupo",
        "Estudiante: subrayá y luego leé",
        "Alumno: completá la tabla con los datos del experimento y compará los resultados.",
    ],
)
def test_no_marca_consignas_en_minuscula(text):
    assert detect(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "Nota: 7",
        "Nota final: 8,50",
        "Calificación: 10",
        "calificacion = 6.5",
        "Nota del trimestre: Aprobado",
        "Calificación final: Muy bueno",
        "Nota: 100",
    ],
)
def test_detecta_calificaciones(text):
    assert _types(text) == ["calificacion"]


def test_planilla_completa_detecta_todos_los_datos_en_orden():
    text = (
        "Planilla de seguimiento\n"
        "Alumno: Juan Pérez\n"
        "DNI: 45.678.901\n"
        "Nota final: 9\n"
    )
    findings = detect(text)
    assert [f.type for f in findings] == ["nombre_rotulado", "dni", "calificacion"]
    assert [f.line for f in findings] == [2, 3, 4]


# --- Materiales educativos que NO deben marcarse (falsos positivos) -------------


@pytest.mark.parametrize(
    "text",
    [
        "Nota: leé el capítulo 2 antes de la clase.",
        "Nota: los ejercicios 3 y 4 son optativos.",
        "Calculá 12.345.678 + 1.000.000 y escribí el resultado en letras.",
        "La población de Argentina supera los 45.000.000 de habitantes.",
        "San Martín cruzó los Andes en 1817.",
        "Leé el cuento de Julio Cortázar y respondé las preguntas.",
        "El documento que vamos a leer tiene 3 partes.",
        "Ejercicio 2 (puntaje: 10). Resolvé la ecuación.",
        "Los estudiantes: deberán entregar el trabajo el viernes.",
        "Alumno: completá la tabla con los datos del experimento.",
        "El Pte. Perón asumió en 1946.",
        "Actividad 1: Leé el texto de la página 8 y respondé las preguntas.",
        "",
        "   ",
    ],
)
def test_no_marca_materiales_educativos_normales(text):
    assert detect(text) == []


def test_no_marca_numero_que_sigue_despues_del_dni():
    # Un DNI de 8 cifras pegado a más cifras no es un DNI (ej. un código largo).
    assert _types("DNI: 123456789012") == []


# --- Enmascarado: el dato completo nunca vuelve al cliente -----------------------


def test_enmascara_dni():
    [finding] = detect("DNI: 12.345.678")
    assert finding.masked == "DNI: 1*.***.**8"
    assert "345" not in finding.masked


def test_enmascara_cuil():
    [finding] = detect("20-12345678-9")
    assert finding.masked == "2*-********-9"


def test_enmascara_nombre():
    [finding] = detect("Alumno: Juan Pérez")
    assert finding.masked == "Alumno: J*** P***"
    assert "Juan" not in finding.masked and "Pérez" not in finding.masked


def test_enmascara_calificacion():
    [finding] = detect("Nota final: 8,50")
    assert finding.masked == "Nota final: ***"


def test_hallazgo_incluye_id_tipo_etiqueta_y_linea():
    [finding] = detect("Introducción\n\nEstudiante: Ana Gómez")
    assert finding.type == "nombre_rotulado"
    assert finding.label == "Nombre de una persona"
    assert finding.line == 3
    assert finding.id == "nombre_rotulado-26"


def test_id_es_estable_y_no_expone_el_dato():
    text = "Alumno: Juan Pérez\nDNI: 30.123.456"
    first = [f.id for f in detect(text)]
    assert first == [f.id for f in detect(text)]
    # Solo tipo + posición: "nombre_rotulado-8", "dni-24". Nunca el dato.
    assert all(re.fullmatch(r"[a-z_]+-\d+", i) for i in first)


def test_id_cambia_si_el_docente_edita_el_material():
    before = detect("Alumno: Juan Pérez")[0].id
    after = detect("Curso 3B\nAlumno: Juan Pérez")[0].id
    assert before != after


# --- ensure_no_sensitive_data / SensitiveDataError -------------------------------


def test_ensure_no_lanza_error_si_el_texto_esta_limpio():
    assert ensure_no_sensitive_data("Actividad 1: Leé el texto y respondé las preguntas.") == []


# --- Descarte de hallazgos por el docente -------------------------------------------

_PLANILLA = "Alumno: Juan Pérez\nDNI: 30.123.456\nNota: 7"


def test_descartar_todos_los_hallazgos_permite_seguir():
    ids = [f.id for f in detect(_PLANILLA)]
    dismissed = ensure_no_sensitive_data(_PLANILLA, dismissed=ids)
    assert [f.id for f in dismissed] == ids


def test_descartar_solo_algunos_sigue_bloqueando_con_los_pendientes():
    findings = detect(_PLANILLA)
    with pytest.raises(SensitiveDataError) as exc_info:
        ensure_no_sensitive_data(_PLANILLA, dismissed=[findings[0].id])
    assert [f.id for f in exc_info.value.findings] == [f.id for f in findings[1:]]


def test_ids_inventados_o_viejos_no_desbloquean():
    with pytest.raises(SensitiveDataError):
        ensure_no_sensitive_data(_PLANILLA, dismissed=["dni-999", "nombre_rotulado-0-x"])


def test_descarte_de_material_anterior_no_vale_si_cambio_el_texto():
    old_ids = [f.id for f in detect(_PLANILLA)]
    with pytest.raises(SensitiveDataError):
        ensure_no_sensitive_data("Curso 3B\n" + _PLANILLA, dismissed=old_ids)


def test_ensure_lanza_error_con_hallazgos():
    with pytest.raises(SensitiveDataError) as exc_info:
        ensure_no_sensitive_data("Alumno: Juan Pérez - DNI 30.123.456")
    assert len(exc_info.value.findings) == 2


def test_error_no_es_value_error():
    # Los endpoints capturan ValueError como 400 genérico: este error necesita su propio manejo.
    assert not issubclass(SensitiveDataError, ValueError)


def test_detail_tiene_formato_acordado_y_no_expone_datos():
    with pytest.raises(SensitiveDataError) as exc_info:
        ensure_no_sensitive_data("Alumno: Juan Pérez\nDNI: 30.123.456\nNota: 7")
    detail = exc_info.value.to_detail()
    assert detail["code"] == "sensitive_data_detected"
    assert detail["total"] == 3
    assert {"type", "label", "masked", "line"} <= set(detail["findings"][0])
    serialized = str(detail)
    assert "Juan" not in serialized and "30.123.456" not in serialized


def test_detail_limita_la_cantidad_de_hallazgos():
    text = "\n".join("Alumno: Juan Pérez" for _ in range(MAX_REPORTED_FINDINGS + 5))
    with pytest.raises(SensitiveDataError) as exc_info:
        ensure_no_sensitive_data(text)
    detail = exc_info.value.to_detail()
    assert detail["total"] == MAX_REPORTED_FINDINGS + 5
    assert len(detail["findings"]) == MAX_REPORTED_FINDINGS
