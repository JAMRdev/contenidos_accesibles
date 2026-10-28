"""
Mapa mental (Issue #118): esquema y límites, conversión de la salida de la IA, lista anidada,
sintaxis Mermaid escapada, edición y exportación. La IA se reemplaza por respuestas fijas.
"""
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core import rate_limit
from app.core.config import settings
from app.db.database import init_db
from app.main import app
from app.schemas.transformation import MindMapAIOutput, MindMapNode, MindMapResult
from app.services import storage_service
from app.services.llm.base import LLMInvalidResponseError, Usage
from app.services.llm_service import StructuredResult
from app.services.storage_service import LocalStorage
from app.services.transformations import mind_map
from app.services.transformations.base import MAX_SOURCE_CHARS, source_for_ai

MATERIAL = (
    "Unidad 3: El ciclo del agua\n\n"
    "El agua de los océanos se evapora por el calor del sol.\n\n"
    "El vapor se condensa y forma las nubes."
)

AI_OUTPUT = {
    "central_topic": "Ciclo del agua",
    "ideas": [
        {"text": "Evaporación", "details": [{"text": "Calor del sol", "subdetails": ["Océanos"]}]},
        {"text": "Condensación", "details": [{"text": "Formación de nubes", "subdetails": []}]},
    ],
}


def _tree(depth: int, width: int = 1) -> dict:
    node = {"text": "hoja", "children": []}
    for _ in range(depth - 1):
        node = {"text": "nodo", "children": [node] * width}
    return node


def _ai_result(output: dict = AI_OUTPUT) -> StructuredResult:
    return StructuredResult(
        parsed=MindMapAIOutput.model_validate(output), provider="groq", model="llama-test", connection_label="Groq",
        latency_ms=120, usage=Usage(prompt_tokens=300, output_tokens=80, total_tokens=380),
    )


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


def _wait(client, transformation_id: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        data = client.get(f"/api/v1/transformations/{transformation_id}").json()
        if data["status"] in ("ready", "failed"):
            return data
        time.sleep(0.02)
    raise AssertionError("La transformación no terminó a tiempo")


def _mind_map(client, *, mock: bool = False, output: dict = AI_OUTPUT) -> dict:
    analysis_id = client.post("/api/v1/analysis/sample?mock=true", json={"text": MATERIAL}).json()["analysis_id"]
    with patch("app.services.llm_service.llm_service.generate_structured", AsyncMock(return_value=_ai_result(output))) as llm:
        res = client.post(f"/api/v1/transformations?mock={str(mock).lower()}",
                          json={"format": "mind_map", "analysis_id": analysis_id})
        assert res.status_code == 202, res.text
        data = _wait(client, res.json()["transformation_id"])
    if not mock:
        assert llm.await_args.kwargs["schema"] is MindMapAIOutput
        assert "evapora" in llm.await_args.kwargs["contents"]
    return data


# --- Esquema y límites ---------------------------------------------------------------


def test_schema_accepts_four_levels_and_rejects_five():
    MindMapResult.model_validate({"root": _tree(4)})
    with pytest.raises(ValidationError, match="4 niveles"):
        MindMapResult.model_validate({"root": _tree(5)})


def test_schema_limits_children_and_text_length():
    with pytest.raises(ValidationError):
        MindMapResult.model_validate({"root": _tree(2, width=7)})
    with pytest.raises(ValidationError):
        MindMapResult.model_validate({"root": {"text": "x" * 81}})
    with pytest.raises(ValidationError):
        MindMapResult.model_validate({"root": {"text": ""}})


def test_ai_output_is_converted_and_clipped():
    long = "palabra " * 30
    output = MindMapAIOutput.model_validate({
        "central_topic": long,
        "ideas": [{"text": f"Idea {i}", "details": []} for i in range(8)] + [{"text": "  ", "details": []}],
    })
    result, warnings = mind_map.from_ai(output)
    assert len(result.root.text) <= 80
    assert result.root.text.endswith("…")
    assert [c.text for c in result.root.children] == [f"Idea {i}" for i in range(6)]
    assert "Se omitieron 2 ramas" in warnings[0]
    assert result.root.depth() == 2


def test_ai_output_without_ideas_is_invalid():
    with pytest.raises(LLMInvalidResponseError):
        mind_map.from_ai(MindMapAIOutput(central_topic="Tema", ideas=[]))


def test_long_material_is_cut_with_a_warning():
    text, warnings = source_for_ai("a" * (MAX_SOURCE_CHARS + 10))
    assert len(text) == MAX_SOURCE_CHARS
    assert "30.000" in warnings[0]
    assert source_for_ai("corto") == ("corto", [])


# --- Lista anidada y Mermaid --------------------------------------------------------------


def _sample() -> MindMapResult:
    return MindMapResult(root=MindMapNode(text="Ciclo del agua", children=[
        MindMapNode(text="Evaporación", children=[MindMapNode(text="Calor del sol")]),
        MindMapNode(text="Condensación"),
    ]))


def test_markdown_is_a_nested_list():
    assert mind_map.to_markdown(_sample()) == (
        "- Ciclo del agua\n"
        "  - Evaporación\n"
        "    - Calor del sol\n"
        "  - Condensación\n"
    )


def test_markdown_escapes_what_would_change_the_list():
    root = MindMapNode(text="Tema", children=[
        MindMapNode(text="1. Paso inicial"), MindMapNode(text="- guion"), MindMapNode(text="*énfasis* y [enlace](x) <b>"),
    ])
    lines = mind_map.to_markdown(MindMapResult(root=root)).splitlines()
    assert lines[1] == "  - 1\\. Paso inicial"
    assert lines[2] == "  - \\- guion"
    assert lines[3] == "  - \\*énfasis\\* y \\[enlace\\](x) \\<b\\>"


def test_text_export_is_indented():
    assert mind_map.to_text(_sample()) == "Ciclo del agua\n- Evaporación\n  - Calor del sol\n- Condensación\n"


def test_mermaid_mindmap_with_hierarchy_by_indentation():
    assert mind_map.to_mermaid(_sample()) == (
        "mindmap\n"
        '  root(("Ciclo del agua"))\n'
        '    n1["Evaporación"]\n'
        '      n2["Calor del sol"]\n'
        '    n3["Condensación"]\n'
    )


def test_mermaid_escapes_special_characters():
    root = MindMapNode(text='El "ciclo" #1 (agua) <h1> `x`', children=[MindMapNode(text="a[b]{c}")])
    mermaid = mind_map.to_mermaid(MindMapResult(root=root))
    assert 'root(("El #34;ciclo#34; #35;1 (agua) #60;h1#62; #96;x#96;"))' in mermaid
    assert 'n1["a[b]{c}"]' in mermaid  # entre comillas, corchetes y llaves no cierran el nodo
    for line in mermaid.splitlines()[1:]:
        label = line.split('"', 1)[1].rsplit('"', 1)[0]
        assert '"' not in label and "<" not in label


# --- Endpoint -----------------------------------------------------------------------------


def test_real_generation_returns_tree_views_and_metadata(client):
    data = _mind_map(client)
    assert data["status"] == "ready"
    assert data["result"]["root"]["text"] == "Ciclo del agua"
    assert [c["text"] for c in data["result"]["root"]["children"]] == ["Evaporación", "Condensación"]
    assert data["metadata"]["provider"] == "groq"
    assert data["metadata"]["total_tokens"] == 380
    assert data["views"]["markdown"].startswith("- Ciclo del agua\n  - Evaporación\n")
    assert data["views"]["mermaid"].startswith("mindmap\n")


def test_invalid_ai_output_fails(client):
    data = _mind_map(client, output={"central_topic": "", "ideas": []})
    assert data["status"] == "failed"
    assert data["error"]["code"] == "invalid_ai_response"


def test_mock_also_has_views(client):
    data = _mind_map(client, mock=True)
    assert data["metadata"]["provider"] == "mock"
    assert data["views"]["markdown"].startswith("- Unidad 3: El ciclo del agua")


def test_edit_is_validated_and_views_follow_the_edit(client):
    data = _mind_map(client)
    url = f"/api/v1/transformations/{data['transformation_id']}/decision"
    res = client.post(url, json={"decision": "editada", "edited_result": {"root": _tree(5)}})
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "invalid_edited_result"

    edited = {"root": {"text": "Agua", "children": [{"text": "Lluvia", "children": []}]}}
    res = client.post(url, json={"decision": "editada", "edited_result": edited})
    assert res.status_code == 200
    assert res.json()["views"]["markdown"] == "- Agua\n  - Lluvia\n"


def test_export_only_after_accepting(client):
    data = _mind_map(client)
    tid = data["transformation_id"]
    assert client.get(f"/api/v1/transformations/{tid}/export?format=md").status_code == 409

    client.post(f"/api/v1/transformations/{tid}/decision", json={"decision": "aceptada"})
    res = client.get(f"/api/v1/transformations/{tid}/export?format=md")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/markdown")
    assert f"mapa_mental_{tid[:8]}.md" in res.headers["content-disposition"]
    assert res.text.startswith("- Ciclo del agua\n")

    res = client.get(f"/api/v1/transformations/{tid}/export?format=txt")
    assert res.text.startswith("Ciclo del agua\n- Evaporación")
    assert client.get(f"/api/v1/transformations/{tid}/export?format=pdf").status_code == 400

    link = client.get(f"/api/v1/transformations/{tid}/export?format=md&delivery=link").json()
    assert link["file"]["kind"] == "export"
    assert link["download"]["url"]


def test_discarded_has_no_views_nor_export(client):
    data = _mind_map(client)
    tid = data["transformation_id"]
    res = client.post(f"/api/v1/transformations/{tid}/decision", json={"decision": "descartada"})
    assert res.json()["views"] is None
    assert client.get(f"/api/v1/transformations/{tid}/export?format=md").status_code == 409
