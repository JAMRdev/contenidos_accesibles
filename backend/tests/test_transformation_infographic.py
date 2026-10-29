"""
Infografía (Issue #119): contenido de la IA, plantillas SVG accesibles (texto real, orden de lectura,
contraste AA) y exportación a SVG, PNG, PDF y Markdown. La IA se reemplaza por respuestas fijas.
"""
import asyncio
import time
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock, patch

import pymupdf
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core import rate_limit
from app.core.config import settings
from app.db.database import init_db
from app.main import app
from app.schemas.transformation import INFOGRAPHIC_ICONS, InfographicAIOutput, InfographicResult
from app.services import storage_service
from app.services.llm.base import LLMInvalidResponseError, Usage
from app.services.llm.openai_adapter import to_strict_json_schema
from app.services.llm_service import StructuredResult
from app.services.storage_service import LocalStorage
from app.services.transformations import infographic
from app.services.transformations import infographic_templates as templates
from app.services.transformations.base import SourceMaterial

SVG_NS = "{http://www.w3.org/2000/svg}"
MATERIAL = (
    "Unidad 3: El ciclo del agua\n\n"
    "El agua de los océanos se evapora por el calor del sol y sube a la atmósfera.\n\n"
    "El vapor se enfría, se condensa y forma las nubes.\n\n"
    "Cuando las gotas pesan, caen como lluvia o nieve y vuelven a los ríos y océanos."
)
CONTENT = {
    "title": "El ciclo del agua",
    "subtitle": "Cómo circula el agua entre el mar, el aire y la tierra.",
    "layout": "pasos",
    "sections": [
        {"title": "Evaporación", "text": "El sol calienta el agua y el vapor sube.", "highlight": "86 % sale del mar", "icon": "naturaleza"},
        {"title": "Condensación", "text": "El vapor se enfría y forma las nubes.", "highlight": "", "icon": "grafico"},
        {"title": "Precipitación", "text": "Las gotas caen como lluvia o nieve.", "highlight": "", "icon": "planeta"},
    ],
    "closing": "El agua que tomamos ya pasó muchas veces por este ciclo.",
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "FILE_CLEANUP_INTERVAL_MINUTES", 0)
    init_db()
    storage_service.reset_storage(LocalStorage(str(tmp_path / "storage"), b"secreto-de-prueba"))
    rate_limit.reset()
    with TestClient(app) as c:
        yield c
    storage_service.reset_storage()
    rate_limit.reset()


def _result(**changes) -> InfographicResult:
    result, _ = infographic.from_ai(InfographicAIOutput.model_validate(CONTENT))
    return result.model_copy(update=changes)


def _ai_result(output: dict = CONTENT) -> StructuredResult:
    return StructuredResult(parsed=InfographicAIOutput.model_validate(output), provider="groq", model="gpt-oss-test",
                            connection_label="Groq", latency_ms=700, usage=Usage(prompt_tokens=900, output_tokens=300, total_tokens=1200))


# --- Contenido de la IA --------------------------------------------------------------------


def test_ai_output_becomes_a_clipped_infographic():
    long = {**CONTENT, "title": "palabra " * 30, "sections": [
        {"title": f"Idea {i}", "text": "texto " * 80, "highlight": "  ", "icon": "info"} for i in range(8)
    ] + [{"title": "  ", "text": "sin título", "highlight": "", "icon": "info"}]}
    result, warnings = infographic.from_ai(InfographicAIOutput.model_validate(long))
    assert len(result.title) <= 90 and result.title.endswith("…")
    assert len(result.sections) == 6 and "se dejaron las primeras 6" in warnings[0]
    assert all(len(s.text) <= 280 and s.highlight is None for s in result.sections)
    result, _ = infographic.from_ai(InfographicAIOutput.model_validate(CONTENT))
    assert result.layout == "pasos" and result.sections[0].highlight == "86 % sale del mar"
    with pytest.raises(LLMInvalidResponseError):
        infographic.from_ai(InfographicAIOutput.model_validate({**CONTENT, "sections": []}))


def test_icons_come_from_a_closed_list_and_the_strict_schema_keeps_title():
    with pytest.raises(ValidationError):
        InfographicAIOutput.model_validate({**CONTENT, "sections": [{**CONTENT["sections"][0], "icon": "cohete"}]})
    schema = to_strict_json_schema(InfographicAIOutput)
    assert schema["required"] == ["title", "subtitle", "layout", "sections", "closing"]
    item = schema["properties"]["sections"]["items"]
    assert item["required"] == ["title", "text", "highlight", "icon"]
    assert item["properties"]["icon"]["enum"] == list(INFOGRAPHIC_ICONS)
    assert set(templates.ICONS) == set(INFOGRAPHIC_ICONS)  # cada ícono de la lista tiene dibujo


def test_generator_asks_for_the_keys_and_the_icon_list():
    with patch("app.services.llm_service.llm_service.generate_structured", AsyncMock(return_value=_ai_result())) as llm:
        generation = asyncio.run(infographic.generate(SourceMaterial(text=MATERIAL)))
    kwargs = llm.await_args.kwargs
    assert kwargs["schema"] is InfographicAIOutput and "se evapora" in kwargs["contents"]
    for key in ("`title`", "`subtitle`", "`layout`", "`sections`", "`highlight`", "`icon`", "`closing`"):
        assert key in kwargs["system_instruction"]
    assert ", ".join(INFOGRAPHIC_ICONS) in kwargs["system_instruction"]
    assert generation.result.title == "El ciclo del agua" and generation.metadata.total_tokens == 1200


# --- Plantillas ----------------------------------------------------------------------------


def _texts(svg: str) -> list[str]:
    root = ET.fromstring(svg)
    return [" ".join(t.itertext()) for t in root.iter(f"{SVG_NS}text")]


@pytest.mark.parametrize("layout", ["vertical", "pasos", "comparativa"])
def test_each_template_is_an_accessible_svg_with_real_text_in_reading_order(layout):
    result = _result(layout=layout)
    svg = templates.render_svg(result)
    root = ET.fromstring(svg)
    assert root.get("role") == "img" and root.get("lang") == "es"
    assert root.get("aria-labelledby") == "infografia-titulo infografia-descripcion"
    assert root.find(f"{SVG_NS}title").text == "El ciclo del agua"
    desc = root.find(f"{SVG_NS}desc").text
    assert desc.index("Evaporación") < desc.index("Condensación") < desc.index("Precipitación") < desc.index("El agua que tomamos")

    text = " ".join(_texts(svg))
    order = ["El ciclo del agua", "Cómo circula", "Evaporación", "86 % sale del mar", "Condensación", "Precipitación", "El agua que tomamos"]
    positions = [text.index(t) for t in order]
    assert positions == sorted(positions)  # en el DOM, en el orden de lectura
    # Lo que no es texto (fondos, tarjetas, íconos) es decorativo
    for tag in ("rect", "circle", "g", "line"):
        assert all(e.get("aria-hidden") == "true" for e in root.findall(f"{SVG_NS}{tag}"))
    if layout == "pasos":
        assert ["1", "2", "3"] == [t for t in _texts(svg) if t.isdigit()]


def _luminance(hex_color: str) -> float:
    def channel(c: int) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("text,background", [
    ("header_text", "header"), ("header_subtitle", "header"), ("section_title", "card"), ("text", "card"),
    ("highlight_text", "highlight"), ("lead_text", "lead"), ("closing_text", "closing"),
])
def test_text_colors_meet_aa_contrast(text, background):
    """WCAG 1.4.3 (AA): 4,5:1 para texto normal, aunque buena parte del texto es grande."""
    assert _contrast(templates.PALETTE[text], templates.PALETTE[background]) >= 4.5


def test_long_text_is_wrapped_inside_its_box_and_escaped():
    width = 400
    lines = templates.wrap("Electroencefalografistas " * 3 + "otorrinolaringológicamente desoxirribonucleico", "text", width)
    assert all(templates.text_width(line, "text") <= width for line in lines)
    assert "".join(lines).replace("-", "").replace(" ", "") == ("Electroencefalografistas" * 3 + "otorrinolaringológicamentedesoxirribonucleico")

    svg = templates.render_svg(_result(title='<script>alert("x")</script> & más', layout="comparativa"))
    ET.fromstring(svg)  # sigue siendo XML válido
    assert "<script>" not in svg and "&lt;script&gt;" in svg


# --- Circuito completo y exportación --------------------------------------------------------


def _wait(client, transformation_id: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        data = client.get(f"/api/v1/transformations/{transformation_id}").json()
        if data["status"] in ("ready", "failed"):
            return data
        time.sleep(0.02)
    raise AssertionError("La transformación no terminó a tiempo")


def _create(client, *, mock: bool) -> dict:
    analysis_id = client.post("/api/v1/analysis/sample?mock=true", json={"text": MATERIAL}).json()["analysis_id"]
    with patch("app.services.llm_service.llm_service.generate_structured", AsyncMock(return_value=_ai_result())):
        res = client.post(f"/api/v1/transformations?mock={str(mock).lower()}",
                          json={"format": "infographic", "analysis_id": analysis_id})
        assert res.status_code == 202, res.text
        return _wait(client, res.json()["transformation_id"])


def test_real_generation_returns_the_svg_and_text_views(client):
    data = _create(client, mock=False)
    assert data["status"] == "ready" and data["metadata"]["model"] == "gpt-oss-test"
    assert data["result"]["layout"] == "pasos"
    assert data["views"]["svg"].startswith("<svg") and "Evaporación" in data["views"]["svg"]
    assert data["views"]["markdown"].startswith("# El ciclo del agua\n\nCómo circula")
    assert "## 1. Evaporación" in data["views"]["markdown"]
    assert data["file"] is None  # se dibuja a pedido: no hay archivo en R2 hasta exportar


def test_mock_infographic(client):
    data = _create(client, mock=True)
    assert data["result"]["title"] == "Unidad 3: El ciclo del agua"
    assert [s["icon"] for s in data["result"]["sections"]] == ["idea", "libro", "check"]


def test_edited_infographic_is_validated_and_drawn_again(client):
    data = _create(client, mock=True)
    url = f"/api/v1/transformations/{data['transformation_id']}/decision"
    bad = {**CONTENT, "sections": [{**CONTENT["sections"][0], "icon": "cohete"}]}
    assert client.post(url, json={"decision": "editada", "edited_result": bad}).status_code == 422
    edited = {**CONTENT, "layout": "comparativa", "sections": [{**s, "highlight": s["highlight"] or None} for s in CONTENT["sections"]]}
    res = client.post(url, json={"decision": "editada", "edited_result": edited})
    assert res.status_code == 200
    assert "Precipitación" in res.json()["views"]["svg"] and res.json()["final_result"]["layout"] == "comparativa"


@pytest.mark.parametrize("fmt,content_type,signature", [
    ("svg", "image/svg+xml", b"<svg"), ("png", "image/png", b"\x89PNG"),
    ("pdf", "application/pdf", b"%PDF-"), ("md", "text/markdown", b"# El ciclo del agua"),
])
def test_accepted_infographic_exports(client, fmt, content_type, signature):
    data = _create(client, mock=False)
    tid = data["transformation_id"]
    client.post(f"/api/v1/transformations/{tid}/decision", json={"decision": "aceptada"})
    res = client.get(f"/api/v1/transformations/{tid}/export?format={fmt}")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith(content_type)
    assert res.content.startswith(signature)
    if fmt == "pdf":
        pdf = pymupdf.open("pdf", res.content)
        text = pdf[0].get_text()
        assert "El ciclo del agua" in text and "Precipitación" in text  # texto real, no una imagen
        assert pdf.metadata["title"] == "El ciclo del agua" and pdf.language == "es"
    if fmt == "png":
        assert pymupdf.Pixmap(res.content).width == 2 * templates.WIDTH
