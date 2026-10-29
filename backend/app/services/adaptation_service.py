"""
Propuestas de adaptación con IA (Issue #27).

Después del diagnóstico, el docente elige una barrera y un tipo de adaptación; el
servicio devuelve original + propuesta + explicación. Las propuestas nunca se
aplican solas: se guardan como 'pendiente' hasta que el docente decide.
"""
import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from app.db import adaptation_repository, analysis_repository
from app.db import user_activity_repository as user_activity
from app.schemas.adaptation import (
    AdaptationAIOutput,
    AdaptationMetadata,
    AdaptationProposal,
    AdaptationRequest,
    AdaptationType,
    AdaptedDocumentResponse,
    BarrierResolutionDetail,
    DecisionLogEntry,
    DecisionRequest,
    FinalReportResponse,
)
from app.schemas.analysis import AccessibilityAnalysisResponse, BarrierReport
from app.services.text_matching import fragment_parts as _fragment_parts, normalize as _normalize
from app.services.llm.base import LLMInvalidResponseError
from app.services.llm_service import llm_service
from app.services.rules_service import RuleFinding, compute_score, run_rules, split_paragraphs, split_sentences
from app.services.sensitive_data_service import ensure_no_sensitive_data

logger = logging.getLogger(__name__)

CONTEXT_CHARS = 600  # caracteres de cada párrafo vecino que se envían como contexto

_TYPE_INSTRUCTIONS: dict[str, str] = {
    "lenguaje_claro": (
        "Reescribí el fragmento en lenguaje claro: oraciones breves (idealmente menos de 20 palabras), "
        "una idea por oración, voz activa, verbos en lugar de nominalizaciones, vocabulario frecuente y "
        "referencias explícitas en lugar de 'esto' o 'lo anterior'. Desarrollá las siglas la primera vez."
    ),
    "consigna_en_pasos": (
        "Dividí la consigna en pasos numerados ('1.', '2.', ...), con una sola acción por paso, empezando "
        "por el verbo y en el orden en que el estudiante debe hacerlas. Si el original indica el producto "
        "esperado, la forma o el plazo de entrega, dejalo en un paso final explícito."
    ),
    "reorganizar": (
        "Reorganizá el contenido extenso en bloques breves de 2 a 4 oraciones separados por una línea en "
        "blanco, cada uno con una idea central. Ordená las ideas de lo general a lo particular y pasá a "
        "lista con '- ' las enumeraciones que estén dentro de una oración. No resumas: conservá toda la información."
    ),
    "mejorar_estructura": (
        "Mejorá la estructura del fragmento: agregá un título breve en un renglón propio por cada bloque "
        "temático, convertí enumeraciones en listas ('- ' o '1.') y separá los bloques con una línea en "
        "blanco. Mantené la redacción original salvo los cambios mínimos que pida la nueva estructura."
    ),
}

SYSTEM_PROMPT = """
Sos un especialista en accesibilidad de materiales educativos, Diseño Universal para el
Aprendizaje (DUA) y lenguaje claro. Un docente de nivel secundario o superior te pide adaptar
un fragmento de su material para reducir una barrera de acceso ya diagnosticada.

## Reglas obligatorias
- Conservá el objetivo pedagógico: la misma tarea, el mismo contenido disciplinar y el mismo
  nivel de exigencia. No agregues ni quites actividades, preguntas, datos ni criterios de
  evaluación. No resuelvas la consigna ni des pistas de la respuesta.
- Mantené la terminología técnica propia de la disciplina. Si un término puede trabar la
  lectura, agregá una aclaración breve entre paréntesis la primera vez que aparece.
- Respetá el registro del original: si usa voseo ("leé", "resolvé"), mantené el voseo.
- No inventes referencias (páginas, autores, fechas, materiales) que no estén en el original.
- Adaptá solo el fragmento. El contexto sirve para entenderlo y no se reescribe.
- Formato de `proposed_text`: texto plano con saltos de línea. Numerá con "1.", viñetas con
  "- " y títulos en un renglón propio. No uses Markdown (#, **, _).
- Redactá `explanation` y `preserved_objective` en español claro y respetuoso hacia el docente.
""".strip()


class AdaptationNotFoundError(LookupError):
    """No existe (o venció) el diagnóstico, la barrera o la propuesta pedida."""


class FragmentRequiredError(ValueError):
    """La barrera no cita un fragmento textual y el docente no envió uno."""


class ProposalStateError(ValueError):
    """La operación no corresponde al estado actual de la propuesta (ej. decidir dos veces)."""


class PendingProposalError(ProposalStateError):
    """La barrera ya tiene una propuesta pendiente (o se está generando una): como mucho una por barrera."""

    CODE = "pending_proposal_exists"

    def __init__(self, message: str, proposal_id: str | None = None):
        super().__init__(message)
        self.proposal_id = proposal_id

    def to_detail(self) -> dict:
        return {"code": self.CODE, "message": str(self), "proposal_id": self.proposal_id}


# Barreras con una propuesta generándose ahora (la IA tarda segundos): evita que un doble clic
# o dos pestañas pasen el control a la vez. En memoria alcanza porque hay una sola réplica.
_generating: set[tuple[str, str]] = set()
_generating_lock = asyncio.Lock()


async def _reserve_barrier(analysis_id: str, barrier_id: str) -> tuple[str, str]:
    key = (analysis_id, barrier_id)
    async with _generating_lock:
        if key in _generating:
            raise PendingProposalError("Ya se está generando una propuesta para esta barrera. Esperá a que termine.")
        pending = await asyncio.to_thread(adaptation_repository.find_pending, analysis_id, barrier_id)
        if pending:
            raise PendingProposalError(
                "Esta barrera ya tiene una propuesta pendiente: aceptala, editala o descartala antes de pedir otra.",
                proposal_id=pending,
            )
        _generating.add(key)
    return key


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_analysis(analysis_id: str, *, owner: str) -> tuple[AccessibilityAnalysisResponse, str]:
    """
    Devuelve el diagnóstico vigente y su texto extraído. Uno vencido se borra al consultarlo.
    El de otro usuario responde igual que uno inexistente (Issue #61).
    """
    record = analysis_repository.get(analysis_id)
    if not record or record["user_id"] != owner:
        raise AdaptationNotFoundError("Diagnóstico no encontrado")
    if record["expires_at"] < _now_utc_iso():
        analysis_repository.delete(analysis_id)
        raise AdaptationNotFoundError("El diagnóstico ha expirado")
    analysis = AccessibilityAnalysisResponse.model_validate_json(record["diagnostic_json"])
    return analysis, record["extracted_text"]


def _is_placeholder(fragment: str) -> bool:
    """Las reglas globales citan '[...]' en vez de un fragmento (ej. '[Documento completo: x.pdf]')."""
    fragment = fragment.strip()
    return fragment.startswith("[") and fragment.endswith("]")


def resolve_fragment(
    barrier: BarrierReport, extracted_text: str, adaptation_type: AdaptationType
) -> tuple[str, list[str]]:
    """
    Devuelve el texto completo a adaptar a partir de la cita de la barrera.

    Las reglas citan los párrafos y consignas largas recortados con '…', y la IA a veces
    abrevia con '...': se recupera del material el párrafo completo que contiene la cita
    (o la oración, si se pide dividir una consigna en pasos).
    """
    original = barrier.original_text.strip()
    if _is_placeholder(original):
        raise FragmentRequiredError(
            f"La barrera '{barrier.id}' es una evaluación global y no cita un fragmento del material: "
            "enviá en 'fragment' el texto que querés adaptar."
        )

    warnings: list[str] = []
    if barrier.fragment_found is False:
        warnings.append(
            "El fragmento citado por la barrera no coincide textualmente con el material: "
            "revisá que la propuesta corresponda al texto que querés adaptar."
        )

    if not re.search(r"\.{3}|…", original):
        return original, warnings

    parts = _fragment_parts(original)
    for paragraph in split_paragraphs(extracted_text):
        if all(p in _normalize(paragraph) for p in parts):
            if adaptation_type == "consigna_en_pasos":
                for sentence in split_sentences(paragraph):
                    if all(p in _normalize(sentence) for p in parts):
                        return sentence, warnings
            return paragraph.strip(), warnings

    warnings.append(
        "No se encontró en el material el texto completo de la cita recortada: se adaptó la cita tal como figura."
    )
    return original, warnings


def _context_around(fragment: str, extracted_text: str) -> str:
    """Párrafos anterior y siguiente al fragmento, recortados, para que la IA entienda el sentido."""
    paragraphs = [p.strip() for p in split_paragraphs(extracted_text) if p.strip()]
    parts = _fragment_parts(fragment)
    if not parts:
        return ""
    for idx, paragraph in enumerate(paragraphs):
        if parts[0] in _normalize(paragraph):
            neighbors = []
            if idx > 0:
                neighbors.append(paragraphs[idx - 1][-CONTEXT_CHARS:])
            if idx + 1 < len(paragraphs):
                neighbors.append(paragraphs[idx + 1][:CONTEXT_CHARS])
            return "\n[...]\n".join(neighbors)
    return ""


def _build_user_prompt(
    barrier: BarrierReport, fragment: str, context: str, adaptation_type: AdaptationType
) -> str:
    context_block = f"Contexto cercano (no lo adaptes):\n<<<\n{context}\n>>>\n\n" if context else ""
    return (
        f"Tipo de adaptación: {adaptation_type}\n"
        f"Instrucciones: {_TYPE_INSTRUCTIONS[adaptation_type]}\n\n"
        f"Barrera detectada: {barrier.issue}\n"
        f"Por qué es una barrera: {barrier.explanation}\n"
        f"Recomendación del diagnóstico: {barrier.recommendation}\n\n"
        f"{context_block}"
        f"Fragmento a adaptar:\n<<<\n{fragment}\n>>>"
    )


# --- Modo mock ---------------------------------------------------------------

_ACTION_SPLIT_RE = re.compile(r",\s*(?:y\s+|e\s+)?|;\s*|\s+y\s+(?=\w+[éáí]\b)")


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _mock_output(fragment: str, adaptation_type: AdaptationType) -> AdaptationAIOutput:
    """Transformaciones determinísticas (0 tokens) que respetan el formato de cada tipo."""
    sentences = split_sentences(fragment) or [fragment]

    if adaptation_type == "consigna_en_pasos":
        actions = []
        for sentence in sentences:
            actions += [a.strip(" .") for a in _ACTION_SPLIT_RE.split(sentence) if a.strip(" .")]
        proposed = "\n".join(f"{i}. {_capitalize(a)}." for i, a in enumerate(actions, start=1))
    elif adaptation_type == "lenguaje_claro":
        clauses = [c.strip(" .") for s in sentences for c in re.split(r",\s+", s) if c.strip(" .")]
        proposed = " ".join(f"{_capitalize(c)}." for c in clauses)
    elif adaptation_type == "reorganizar":
        blocks = [" ".join(sentences[i:i + 3]) for i in range(0, len(sentences), 3)]
        proposed = "\n\n".join(blocks)
    else:  # mejorar_estructura
        proposed = "Título sugerido\n" + "\n".join(f"- {s}" for s in sentences)

    return AdaptationAIOutput(
        proposed_text=proposed,
        explanation=f"[MOCK] Propuesta simulada de tipo '{adaptation_type}' para pruebas de Frontend.",
        preserved_objective="[MOCK] Se conserva la misma tarea y el mismo contenido del fragmento original.",
    )


# --- Punto de entrada ----------------------------------------------------------


async def create_proposal(
    request: AdaptationRequest, *, owner: str, mock: bool = False, dismissed_findings: list[str] | None = None,
) -> AdaptationProposal:
    # Issue #66: el material del diagnóstico ya pasó por el detector, pero el docente puede
    # escribir un fragmento nuevo. Se revisa antes de buscar el diagnóstico y de llamar a la IA.
    if request.fragment:
        ensure_no_sensitive_data(request.fragment, dismissed_findings or ())

    # Base de datos fuera del event loop: con D1 cada consulta es una llamada de red
    analysis, extracted_text = await asyncio.to_thread(load_analysis, request.analysis_id, owner=owner)
    barrier = next((b for b in analysis.barriers if b.id == request.barrier_id), None)

    if barrier is None:
        raise AdaptationNotFoundError(
            f"La barrera '{request.barrier_id}' no existe en el diagnóstico '{request.analysis_id}'."
        )

    if request.fragment:
        fragment, warnings = request.fragment.strip(), []
    else:
        fragment, warnings = resolve_fragment(barrier, extracted_text, request.adaptation_type)

    # Una propuesta pendiente por barrera: se controla antes de llamar a la IA (no gasta cuota)
    reserved = await _reserve_barrier(request.analysis_id, barrier.id)
    try:
        return await _generate_and_save(
            request, barrier, analysis, extracted_text, fragment, warnings, owner=owner, mock=mock,
        )
    finally:
        _generating.discard(reserved)


async def _generate_and_save(
    request: AdaptationRequest,
    barrier: BarrierReport,
    analysis: AccessibilityAnalysisResponse,
    extracted_text: str,
    fragment: str,
    warnings: list[str],
    *,
    owner: str,
    mock: bool,
) -> AdaptationProposal:
    if mock:
        output = _mock_output(fragment, request.adaptation_type)
        warnings.append("Propuesta generada en modo simulación (?mock=true). No se consumieron tokens de IA.")
        metadata = AdaptationMetadata(
            provider="mock",
            model="mock-adaptation-v1",
            connection="Simulador Frontend (?mock=true)",
            latency_ms=1,
            prompt_tokens=0,
            output_tokens=0,
            thinking_tokens=0,
            total_tokens=0,
        )
    else:
        result = await llm_service.generate_structured(
            system_instruction=SYSTEM_PROMPT,
            contents=_build_user_prompt(
                barrier, fragment, _context_around(fragment, extracted_text), request.adaptation_type
            ),
            schema=AdaptationAIOutput,
            temperature=0.3,
        )
        output = result.parsed
        if not output.proposed_text.strip():
            raise LLMInvalidResponseError("El modelo devolvió una propuesta vacía.")
        usage = result.usage
        metadata = AdaptationMetadata(
            provider=result.provider,
            model=result.model,
            connection=result.connection_label,
            latency_ms=result.latency_ms,
            prompt_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            thinking_tokens=usage.thinking_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
        )

    if _normalize(output.proposed_text) == _normalize(fragment):
        warnings.append("La propuesta es igual al fragmento original: probá con otro tipo de adaptación.")

    proposal = AdaptationProposal(
        proposal_id=str(uuid.uuid4()),
        analysis_id=request.analysis_id,
        barrier_id=barrier.id,
        adaptation_type=request.adaptation_type,
        original_text=fragment,
        fragment_source="request" if request.fragment else "barrier",
        proposed_text=output.proposed_text.strip(),
        explanation=output.explanation,
        preserved_objective=output.preserved_objective,
        warnings=warnings,
        created_at=_now_utc_iso(),
        # La propuesta no puede sobrevivir al diagnóstico ni al texto original que adapta.
        expires_at=analysis.expires_at or _now_utc_iso(),
        metadata=metadata,
    )
    await asyncio.to_thread(
        adaptation_repository.save,
        proposal_id=proposal.proposal_id,
        analysis_id=proposal.analysis_id,
        barrier_id=proposal.barrier_id,
        user_id=owner,
        status=proposal.status,
        proposal_json=proposal.model_dump_json(),
        created_at=proposal.created_at,
        expires_at=proposal.expires_at,
    )
    await asyncio.to_thread(
        user_activity.record,
        "proposal",
        owner,
        format=analysis.metadata.source_type,
        adaptation_type=proposal.adaptation_type,
        mock=mock,
        provider=metadata.provider,
        model=metadata.model,
        latency_ms=metadata.latency_ms,
    )
    return proposal


def get_proposal(proposal_id: str, *, owner: str) -> AdaptationProposal:
    record = adaptation_repository.get(proposal_id)
    if not record or record["user_id"] != owner or record["expires_at"] < _now_utc_iso():
        raise AdaptationNotFoundError("Propuesta no encontrada o vencida")
    return AdaptationProposal.model_validate_json(record["proposal_json"])


def list_proposals(analysis_id: str, *, owner: str) -> list[AdaptationProposal]:
    load_analysis(analysis_id, owner=owner)  # 404 si el diagnóstico no existe, venció o es de otro usuario
    return [
        AdaptationProposal.model_validate_json(r["proposal_json"])
        for r in adaptation_repository.list_by_analysis(analysis_id)
    ]


# --- Revisión docente (Issue #29) ---------------------------------------------------


def _apply_transition(
    proposal: AdaptationProposal,
    *,
    owner: str,
    action: str,
    to_status: str,
    edited_text: str | None,
    final_text: str | None,
) -> AdaptationProposal:
    """Persiste el nuevo estado y su entrada en el registro. original_text nunca se toca."""
    now = _now_utc_iso()
    updated = proposal.model_copy(update={
        "status": to_status,
        "edited_text": edited_text,
        "final_text": final_text,
        "decided_at": now if to_status != "pendiente" else None,
    })
    applied = adaptation_repository.transition(
        proposal.proposal_id,
        from_status=proposal.status,
        to_status=to_status,
        proposal_json=updated.model_dump_json(),
        log_entry={
            "proposal_id": proposal.proposal_id,
            "analysis_id": proposal.analysis_id,
            "barrier_id": proposal.barrier_id,
            "user_id": owner,
            "adaptation_type": proposal.adaptation_type,
            "action": action,
            "from_status": proposal.status,
            "to_status": to_status,
            "edited_text": edited_text,
            "created_at": now,
            "expires_at": proposal.expires_at,
        },
    )
    if not applied:
        raise ProposalStateError("La propuesta cambió de estado mientras se procesaba la decisión: volvé a consultarla.")
    # Sin el texto editado: al historial solo va qué se decidió y sobre qué tipo de adaptación
    user_activity.record(
        "decision", owner, detail="deshacer" if action == "deshacer" else to_status,
        adaptation_type=proposal.adaptation_type,
    )
    return updated


def decide(proposal_id: str, request: DecisionRequest, *, owner: str) -> AdaptationProposal:
    """Registra la decisión del docente sobre una propuesta pendiente."""
    proposal = get_proposal(proposal_id, owner=owner)
    if proposal.status != "pendiente":
        raise ProposalStateError(
            f"La propuesta ya fue {proposal.status}: deshacé esa decisión antes de tomar otra."
        )
    final_text = {
        "aceptada": proposal.proposed_text,
        "editada": request.edited_text.strip() if request.edited_text else None,
        "descartada": None,
    }[request.decision]
    return _apply_transition(
        proposal,
        owner=owner,
        action="decision",
        to_status=request.decision,
        edited_text=final_text if request.decision == "editada" else None,
        final_text=final_text,
    )


def undo(proposal_id: str, *, owner: str) -> AdaptationProposal:
    """Revierte la decisión vigente: la propuesta vuelve a 'pendiente'."""
    proposal = get_proposal(proposal_id, owner=owner)
    if proposal.status == "pendiente":
        raise ProposalStateError("La propuesta está pendiente: no hay ninguna decisión para deshacer.")
    other = adaptation_repository.find_pending(proposal.analysis_id, proposal.barrier_id, exclude_id=proposal.proposal_id)
    if other:
        raise PendingProposalError(
            "La barrera ya tiene otra propuesta pendiente: decidí esa antes de deshacer esta.", proposal_id=other,
        )
    return _apply_transition(
        proposal, owner=owner, action="deshacer", to_status="pendiente", edited_text=None, final_text=None,
    )


def list_decisions(analysis_id: str, *, owner: str) -> list[DecisionLogEntry]:
    load_analysis(analysis_id, owner=owner)  # 404 si el diagnóstico no existe, venció o es de otro usuario
    return [DecisionLogEntry(**r) for r in adaptation_repository.list_decisions(analysis_id)]


# --- Generar versión final adaptada (Issue #38) -----------------------------------


@dataclass
class _ReplacementCandidate:
    proposal: AdaptationProposal
    start: int
    end: int
    replacement_text: str


def _find_candidate_span(
    text: str,
    fragment: str,
    used_spans: set[tuple[int, int]],
) -> tuple[int, int] | None:
    """
    Encuentra la posición (inicio, fin) de un fragmento en el texto original.
    Evita reutilizar el mismo span si varias propuestas comparten fragmento idéntico.

    Si el fragmento aparece en el material pero todas sus apariciones ya las tomó otra
    propuesta (p. ej. dos barreras que citan la misma oración), devuelve esa aparición
    igual: el solapamiento se resuelve después por prioridad y queda en warnings, en
    lugar de informarse como "no encontrado".
    """
    if not fragment or not fragment.strip():
        return None

    taken: tuple[int, int] | None = None

    # 1. Búsqueda exacta
    start = 0
    while True:
        idx = text.find(fragment, start)
        if idx == -1:
            break
        span = (idx, idx + len(fragment))
        if span not in used_spans:
            return span
        taken = taken or span
        start = idx + 1

    # 2. Búsqueda con strip()
    stripped = fragment.strip()
    start = 0
    while True:
        idx = text.find(stripped, start)
        if idx == -1:
            break
        span = (idx, idx + len(stripped))
        if span not in used_spans:
            return span
        taken = taken or span
        start = idx + 1

    # 3. Búsqueda con espacios flexibles (regex)
    words = [re.escape(w) for w in re.split(r"\s+", stripped) if w]
    if words:
        pattern = re.compile(r"\s+".join(words))
        for match in pattern.finditer(text):
            span = match.span()
            if span not in used_spans:
                return span
            taken = taken or span

    return taken


def build_adapted_document(analysis_id: str, *, owner: str) -> AdaptedDocumentResponse:
    """
    Construye la versión final del material aplicando únicamente las adaptaciones aceptadas y editadas (Issue #38).

    Reglas:
    - Solo propuestas con status 'aceptada' o 'editada'.
    - Si está 'editada', se respeta textualmente el texto del docente ('edited_text').
    - Si está 'aceptada', se aplica 'proposed_text'.
    - Las propuestas 'descartada' o 'pendiente' se omiten (el fragmento original se preserva).
    - Solapamientos: si dos propuestas colisionan en los mismos caracteres, se prioriza la
      propuesta editada por el docente ('editada' > 'aceptada') y la decisión más reciente.
      La propuesta no elegida se omite y se documenta en warnings.
    - Reemplazo seguro: de derecha a izquierda (índices decrecientes) para no desfasar coordenadas.
    """
    _analysis, extracted_text = load_analysis(analysis_id, owner=owner)
    proposals = list_proposals(analysis_id, owner=owner)

    warnings: list[str] = []
    used_spans: set[tuple[int, int]] = set()
    candidates: list[_ReplacementCandidate] = []

    # Filtrar únicamente aceptadas y editadas
    for p in proposals:
        if p.status not in ("aceptada", "editada"):
            continue

        if p.status == "editada":
            replacement = p.edited_text if p.edited_text is not None else (p.final_text or "")
        else:
            replacement = p.proposed_text if p.proposed_text is not None else (p.final_text or "")

        span = _find_candidate_span(extracted_text, p.original_text, used_spans)
        if span is None:
            warnings.append(
                f"No se encontró en el material el fragmento original de la propuesta '{p.proposal_id}' "
                f"({p.barrier_id}). No se aplicó."
            )
            continue

        used_spans.add(span)
        candidates.append(
            _ReplacementCandidate(
                proposal=p,
                start=span[0],
                end=span[1],
                replacement_text=replacement,
            )
        )

    # Prioridad para resolver solapamientos:
    # 1) status == "editada" > "aceptada" (criterio docente sobre IA)
    # 2) decisión más reciente (decided_at descendente)
    # 3) desempate por proposal_id
    def _cand_priority(cand: _ReplacementCandidate) -> tuple:
        is_edited = 1 if cand.proposal.status == "editada" else 0
        timestamp = cand.proposal.decided_at or cand.proposal.created_at or ""
        return (is_edited, timestamp, cand.proposal.proposal_id)

    sorted_candidates = sorted(candidates, key=_cand_priority, reverse=True)

    # Selección voraz de intervalos sin solapamientos
    selected: list[_ReplacementCandidate] = []
    for cand in sorted_candidates:
        conflicting = next(
            (sel for sel in selected if max(cand.start, sel.start) < min(cand.end, sel.end)),
            None,
        )
        if conflicting:
            warnings.append(
                f"Propuesta '{cand.proposal.proposal_id}' ({cand.proposal.barrier_id}, {cand.proposal.adaptation_type}) "
                f"no aplicada por solapamiento con la propuesta '{conflicting.proposal.proposal_id}'."
            )
        else:
            selected.append(cand)

    # Reemplazo de atrás hacia adelante (Right-to-Left)
    selected.sort(key=lambda c: c.start, reverse=True)
    adapted_text = extracted_text
    for item in selected:
        adapted_text = (
            adapted_text[:item.start]
            + item.replacement_text
            + adapted_text[item.end:]
        )

    applied_proposal_ids = [item.proposal.proposal_id for item in selected]

    return AdaptedDocumentResponse(
        analysis_id=analysis_id,
        original_text=extracted_text,
        adapted_text=adapted_text,
        total_proposals=len(proposals),
        applied_count=len(selected),
        discarded_count=sum(1 for p in proposals if p.status == "descartada"),
        pending_count=sum(1 for p in proposals if p.status == "pendiente"),
        applied_proposals=applied_proposal_ids,
        warnings=warnings,
    )


# --- Informe final (Issue #39) ------------------------------------------------

# Reglas que dependen del archivo y no del texto (imágenes, escaneo, títulos y legibilidad visual del
# PDF/Word/PowerPoint): adaptar el texto no las cambia y el texto plano no permite recalcularlas, así
# que se conservan.
_FILE_RULES = {
    "pdf_escaneado", "imagenes_sin_verificar", "pdf_sin_titulos", "salto_jerarquia_titulos", "secciones_extensas",
    # Issue #79: legibilidad visual del archivo (letra chica, contraste y tipografía)
    "letra_chica_cuerpo", "letra_chica_pasajes", "contraste_insuficiente",
    "tipografia_fuente_decorativa", "tipografia_cursiva", "tipografia_mayusculas", "tipografia_subrayado",
    "tipografia_justificado", "tipografia_interlineado",
}
# De esas, las que siempre son barrera grave (aplican el tope de puntaje). Las que lo son solo a veces
# (contraste < 3:1, fuente principal decorativa) lo indican con ScorePenalty.severe: los puntos no
# alcanzan para saberlo, porque dos casos leves pueden sumar lo mismo que uno grave.
_SEVERE_FILE_RULES = {"pdf_sin_titulos", "letra_chica_cuerpo"}


def _text_doc_from_text(text: str) -> "ExtractedDocument":
    """Construye un ExtractedDocument mínimo a partir de texto plano, para pasarlo al motor de reglas."""
    from app.schemas.document import ExtractedDocument, DocumentStructure
    from app.services.rules_service import count_words
    return ExtractedDocument(
        source_type="text",
        filename=None,
        raw_text=text,
        text_for_analysis=text,
        is_truncated=False,
        page_count=1,
        word_count=count_words(text),
        character_count=len(text),
        has_images=False,
        image_count=0,
        is_scanned_pdf=False,
        structure=DocumentStructure(),
    )


def _score_after(before: "ScoreBreakdown | None", adapted_text: str) -> "ScoreBreakdown":
    """
    Puntaje del material adaptado, comparable con el del diagnóstico original:

    - La IA no se vuelve a consultar: se conserva su puntaje y su peso.
    - Las reglas de texto se recalculan sobre el texto adaptado.
    - Las reglas del archivo (_FILE_RULES), incluida la legibilidad visual del Issue #79, se conservan
      del diagnóstico original: el material adaptado mantiene el formato del archivo original.

    Así la diferencia solo refleja los cambios aplicados: sin cambios, el puntaje es el mismo.
    Comparar contra un puntaje de solo reglas sobre texto plano mostraba mejoras o caídas que
    no ocurrieron (sin el peso de la IA y sin las penalizaciones de imágenes o escaneo).
    """
    rules_after = run_rules(_text_doc_from_text(adapted_text))
    if before is None:  # diagnósticos viejos sin desglose: solo queda el motor de reglas
        return compute_score(ai_score=None, rules=rules_after)

    carried = [p for p in before.penalties if p.rule in _FILE_RULES]
    findings = list(rules_after.findings)
    if any(p.rule in _SEVERE_FILE_RULES or p.severe for p in carried):
        findings.append(RuleFinding(barrier={"severity": "barrera_detectada"}))
    rules = replace(
        rules_after,
        findings=findings,
        penalties=carried + rules_after.penalties,
        is_scanned_pdf=any(p.rule == "pdf_escaneado" for p in carried),
    )
    return compute_score(ai_score=before.ai_score, rules=rules)


_ADAPTATION_TYPE_LABELS: dict[str, str] = {
    "lenguaje_claro": "simplificación de lenguaje",
    "consigna_en_pasos": "división de consignas en pasos",
    "reorganizar": "reorganización de contenido",
    "mejorar_estructura": "mejora de estructura",
}


def _generate_executive_summary(
    barriers_detail: list[BarrierResolutionDetail],
    proposals: list[AdaptationProposal],
) -> list[str]:
    """
    Genera frases descriptivas legibles para el docente que resumen los cambios aplicados.
    Agrupa por tipo de adaptación y categoría de barrera.
    """
    summary: list[str] = []

    # Contar cambios aplicados por tipo de adaptación
    applied_by_type: dict[str, int] = {}
    for p in proposals:
        if p.status in ("aceptada", "editada"):
            label = _ADAPTATION_TYPE_LABELS.get(p.adaptation_type, p.adaptation_type)
            applied_by_type[label] = applied_by_type.get(label, 0) + 1

    for label, count in sorted(applied_by_type.items(), key=lambda x: -x[1]):
        if count == 1:
            summary.append(f"Se aplicó 1 adaptación de {label}.")
        else:
            summary.append(f"Se aplicaron {count} adaptaciones de {label}.")

    # Barreras sin decisión
    pending = sum(1 for b in barriers_detail if b.resolution_status == "pendiente")
    discarded = sum(1 for b in barriers_detail if b.resolution_status == "descartada")

    if pending > 0:
        summary.append(
            f"Quedaron {pending} barrera(s) sin decisión tomada: el texto original se preserva en esas secciones."
        )
    if discarded > 0:
        summary.append(
            f"El docente optó por mantener el texto original en {discarded} barrera(s) detectada(s)."
        )
    if not summary:
        summary.append("No se aplicaron adaptaciones en este material.")

    return summary


def build_final_report(analysis_id: str, *, owner: str) -> FinalReportResponse:
    """
    Genera el informe ejecutivo de cierre del proceso de adaptación (Issue #39).

    - score_before: tomado directamente del diagnóstico original (no se recalcula).
    - score_after: comparable con score_before (ver _score_after); sin cambios aplicados, es igual.
    - barriers_detail: cada barrera del diagnóstico original vinculada con la decisión docente.
    - executive_summary: frases legibles que describen los cambios realizados.
    """
    analysis, extracted_text = load_analysis(analysis_id, owner=owner)
    proposals = list_proposals(analysis_id, owner=owner)

    # ---- Puntaje antes -------------------------------------------------------
    score_before = analysis.estimated_score
    breakdown_before = analysis.score_breakdown  # puede ser None si es muy antiguo

    # ---- Texto adaptado y puntaje después (0 tokens, determinístico) ---------
    adapted = build_adapted_document(analysis_id, owner=owner)
    breakdown_after = _score_after(breakdown_before, adapted.adapted_text)
    score_after = breakdown_after.final_score

    # ---- Mapear barreras a decisiones docentes --------------------------------
    # Índice: barrier_id -> propuesta más relevante (editada > aceptada > descartada > pendiente)
    _STATUS_PRIORITY = {"editada": 0, "aceptada": 1, "descartada": 2, "pendiente": 3}
    proposal_by_barrier: dict[str, AdaptationProposal] = {}
    for p in sorted(proposals, key=lambda x: _STATUS_PRIORITY.get(x.status, 9)):
        if p.barrier_id not in proposal_by_barrier:
            proposal_by_barrier[p.barrier_id] = p

    barriers_detail: list[BarrierResolutionDetail] = []
    for barrier in analysis.barriers:
        p = proposal_by_barrier.get(barrier.id)
        if p is None:
            status: str = "pendiente"
            final_text = None
            proposal_id = None
        else:
            status = p.status
            final_text = p.final_text
            proposal_id = p.proposal_id

        barriers_detail.append(BarrierResolutionDetail(
            barrier_id=barrier.id,
            category=barrier.category,
            severity=barrier.severity,
            issue=barrier.issue,
            original_text=barrier.original_text,
            resolution_status=status,
            final_text=final_text,
            proposal_id=proposal_id,
        ))

    # ---- Métricas consolidadas -----------------------------------------------
    resolved_count = sum(1 for b in barriers_detail if b.resolution_status in ("aceptada", "editada"))
    discarded_count = sum(1 for b in barriers_detail if b.resolution_status == "descartada")
    pending_count = sum(1 for b in barriers_detail if b.resolution_status == "pendiente")

    # ---- Resumen ejecutivo ---------------------------------------------------
    executive_summary = _generate_executive_summary(barriers_detail, proposals)

    return FinalReportResponse(
        analysis_id=analysis_id,
        generated_at=_now_utc_iso(),
        score_before=score_before,
        score_after=score_after,
        score_delta=score_after - score_before,
        score_breakdown_before=breakdown_before,
        score_breakdown_after=breakdown_after,
        executive_summary=executive_summary,
        total_barriers=len(analysis.barriers),
        resolved_count=resolved_count,
        discarded_count=discarded_count,
        pending_count=pending_count,
        total_proposals=len(proposals),
        barriers_detail=barriers_detail,
        warnings=adapted.warnings,
    )
