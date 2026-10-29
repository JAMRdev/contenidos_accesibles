"""
Métricas del panel de administración (/admin).

Junta en un solo JSON lo que muestran los gráficos y tablas del panel:
- Uso de la IA a lo largo del tiempo (tabla ai_usage_events).
- Estado y consumo de cada conexión (tabla api_keys).
- Uso del producto en las últimas horas (diagnósticos, propuestas y transformaciones vigentes).
- Archivos y enlaces compartidos en el período (tabla file_events y enlaces de file_shares).

Nunca devuelve el material analizado, los textos propuestos, las API keys ni nombres de archivo.
"""
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from app.db import ai_usage_repository, file_event_repository
from app.db.database import get_connection
from app.services import key_rotation_service

RANGES = {
    "24h": (timedelta(hours=24), "hour"),
    "7d": (timedelta(days=7), "day"),
    "30d": (timedelta(days=30), "day"),
}

# La paleta categórica del panel tiene 4 colores validados para daltonismo:
# más allá de 3 proveedores, el resto se agrupa en "Otros".
MAX_PROVIDER_SERIES = 3
OTHERS = "otros"

CATEGORIES = ["comprension", "estructura", "accesibilidad_visual"]
SEVERITIES = ["para_revisar", "barrera_detectada"]
ADAPTATION_TYPES = ["lenguaje_claro", "consigna_en_pasos", "reorganizar", "mejorar_estructura"]
PROPOSAL_STATUSES = ["aceptada", "editada", "descartada", "pendiente"]
SOURCE_TYPES = ["text", "pdf", "docx", "pptx"]
TRANSFORMATION_FORMATS = ["no_images", "mind_map", "infographic", "audio"]
# Revisión del docente; 'pendiente' incluye las que se están generando
TRANSFORMATION_STATES = ["aceptada", "editada", "descartada", "pendiente", "fallida"]
RECENT_ANALYSES = 15
FILE_FORMATS = ["pdf", "docx", "pptx", "md", "txt"]
FILE_KINDS = ["upload", "export"]
SHARE_STATES = ["activo", "revocado", "vencido"]
EXPORT_DOCUMENTS = ["content", "report", "transformation"]
EXPORT_DELIVERIES = ["file", "link"]
RECENT_SHARES = 15
# Series del gráfico de actividad (4 colores validados de la paleta categórica)
FILE_ACTIVITY = ["upload", "export", "download", "share_opened"]


def _percentile(values: list[int], pct: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(pct / 100 * len(ordered)) - 1)
    return ordered[index]


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.rstrip("Z")).replace(tzinfo=timezone.utc)


def _bucket_starts(end: datetime, span: timedelta, unit: str, tz_offset: timedelta) -> list[datetime]:
    """Inicio (en UTC) de cada intervalo, alineado a la hora o al día local del navegador."""
    step = timedelta(hours=1) if unit == "hour" else timedelta(days=1)
    local_end = end - tz_offset
    if unit == "hour":
        last = local_end.replace(minute=0, second=0, microsecond=0)
    else:
        last = local_end.replace(hour=0, minute=0, second=0, microsecond=0)
    count = int(span / step)
    return [last - step * i + tz_offset for i in reversed(range(count))]


def _bucket_index(moment: datetime, starts: list[datetime], step: timedelta) -> int | None:
    if moment < starts[0] or moment >= starts[-1] + step:
        return None
    return min(int((moment - starts[0]) / step), len(starts) - 1)


def _usage_block(events: list[dict], starts: list[datetime], step: timedelta) -> dict:
    n = len(starts)
    calls = [0] * n
    tokens = [0] * n
    errors = [0] * n
    outcomes = {o: [0] * n for o in ai_usage_repository.OUTCOMES}
    tokens_by_provider: dict[str, list[int]] = defaultdict(lambda: [0] * n)
    for e in events:
        i = _bucket_index(_parse_utc(e["created_at"]), starts, step)
        if i is None:
            continue
        calls[i] += 1
        tokens[i] += e["total_tokens"]
        outcomes[e["outcome"]][i] += 1
        if e["outcome"] != "ok":
            errors[i] += 1
        tokens_by_provider[e["provider"]][i] += e["total_tokens"]

    ranked = sorted(tokens_by_provider, key=lambda p: (-sum(tokens_by_provider[p]), p))
    series = [{"key": p, "values": tokens_by_provider[p]} for p in ranked[:MAX_PROVIDER_SERIES]]
    if len(ranked) > MAX_PROVIDER_SERIES:
        rest = [sum(tokens_by_provider[p][i] for p in ranked[MAX_PROVIDER_SERIES:]) for i in range(n)]
        series.append({"key": OTHERS, "values": rest})

    return {"calls": calls, "tokens": tokens, "errors": errors, "outcomes": outcomes, "tokens_by_provider": series}


def _tts(events: list[dict], starts: list[datetime], step: timedelta) -> dict:
    """Texto a voz (Issue #121): llamadas, caracteres y minutos de audio por intervalo."""
    n = len(starts)
    calls, characters, minutes, errors = [0] * n, [0] * n, [0.0] * n, [0] * n
    for e in events:
        if e["kind"] != "tts":
            continue
        i = _bucket_index(_parse_utc(e["created_at"]), starts, step)
        if i is None:
            continue
        calls[i] += 1
        characters[i] += e["characters"]
        minutes[i] += e["audio_seconds"] / 60
        if e["outcome"] != "ok":
            errors[i] += 1
    return {
        "calls": calls, "characters": characters, "minutes": [round(m, 1) for m in minutes], "errors": errors,
        "totals": {"calls": sum(calls), "characters": sum(characters), "minutes": round(sum(minutes), 1),
                   "errors": sum(errors)},
    }


def _kpis(events: list[dict], previous: list[dict], usage: dict) -> dict:
    def summary(rows: list[dict]) -> dict:
        ok_latencies = [r["latency_ms"] for r in rows if r["outcome"] == "ok"]
        failed = sum(1 for r in rows if r["outcome"] != "ok")
        return {
            "calls": len(rows),
            "tokens": sum(r["total_tokens"] for r in rows),
            "latency_p95": _percentile(ok_latencies, 95),
            "error_rate": round(failed / len(rows) * 100, 1) if rows else None,
        }

    now, before = summary(events), summary(previous)
    spark = {
        "calls": usage["calls"],
        "tokens": usage["tokens"],
        "latency_p95": None,
        "error_rate": [
            round(e / c * 100, 1) if c else None for e, c in zip(usage["errors"], usage["calls"], strict=True)
        ],
    }
    return {k: {"value": now[k], "previous": before[k], "spark": spark[k]} for k in now}


def _latency_by_model(events: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for e in events:
        if e["outcome"] == "ok":
            groups[(e["provider"], e["model"])].append(e["latency_ms"])
    rows = [
        {"provider": p, "model": m, "calls": len(v), "p50": _percentile(v, 50), "p95": _percentile(v, 95)}
        for (p, m), v in groups.items()
    ]
    return sorted(rows, key=lambda r: -r["p95"])


def _connections(events: list[dict]) -> list[dict]:
    with get_connection() as conn:
        tokens_today = {
            r["id"]: (r["prompt_token_count"], r["candidates_token_count"])
            for r in conn.execute("SELECT id, prompt_token_count, candidates_token_count FROM api_keys")
        }
    latencies: dict[int, list[int]] = defaultdict(list)
    outcomes: dict[int, Counter] = defaultdict(Counter)
    for e in events:
        if e["connection_id"] is None:
            continue
        outcomes[e["connection_id"]][e["outcome"]] += 1
        if e["outcome"] == "ok":
            latencies[e["connection_id"]].append(e["latency_ms"])

    rows = []
    for k in key_rotation_service.list_status_for_dashboard():
        prompt, output = tokens_today.get(k["id"], (0, 0))
        counts = outcomes.get(k["id"], Counter())
        rows.append(
            {
                **k,
                "prompt_tokens_today": prompt,
                "output_tokens_today": output,
                "calls_in_range": sum(counts.values()),
                "errors_in_range": sum(v for o, v in counts.items() if o != "ok"),
                "latency_p50": _percentile(latencies.get(k["id"], []), 50),
                "latency_p95": _percentile(latencies.get(k["id"], []), 95),
            }
        )
    return rows


def _product(now: datetime) -> dict:
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    with get_connection() as conn:
        analyses = conn.execute(
            "SELECT id, source_type, diagnostic_json, created_at FROM analyses "
            "WHERE expires_at > ? ORDER BY created_at DESC",
            (now_iso,),
        ).fetchall()
        proposals = conn.execute(
            "SELECT status, proposal_json FROM adaptation_proposals WHERE expires_at > ?",
            (now_iso,),
        ).fetchall()
        transformation_rows = conn.execute(
            "SELECT format, status, review_status, COUNT(*) AS n FROM transformations WHERE expires_at > ? "
            "GROUP BY format, status, review_status",
            (now_iso,),
        ).fetchall()

    scores: list[int] = []
    histogram = [0] * 10
    by_source = Counter()
    barriers = {c: {s: 0 for s in SEVERITIES} for c in CATEGORIES}
    recent = []
    for row in analyses:
        try:
            d = json.loads(row["diagnostic_json"])
        except (TypeError, ValueError):
            continue
        score = d.get("estimated_score")
        meta = d.get("metadata") or {}
        if isinstance(score, int):
            scores.append(score)
            histogram[min(score // 10, 9)] += 1
        by_source[row["source_type"]] += 1
        for b in d.get("barriers") or []:
            if b.get("category") in barriers and b.get("severity") in SEVERITIES:
                barriers[b["category"]][b["severity"]] += 1
        if len(recent) < RECENT_ANALYSES:
            recent.append(
                {
                    "id": row["id"],
                    "created_at": row["created_at"],
                    "source_type": row["source_type"],
                    "score": score,
                    "barriers": len(d.get("barriers") or []),
                    "provider": meta.get("provider"),
                    "model": meta.get("model"),
                    "latency_ms": meta.get("latency_ms"),
                    "total_tokens": meta.get("total_tokens"),
                }
            )

    decisions = {t: {s: 0 for s in PROPOSAL_STATUSES} for t in ADAPTATION_TYPES}
    for p in proposals:
        try:
            adaptation_type = json.loads(p["proposal_json"]).get("adaptation_type")
        except (TypeError, ValueError):
            continue
        if adaptation_type in decisions and p["status"] in PROPOSAL_STATUSES:
            decisions[adaptation_type][p["status"]] += 1

    # Transformaciones vigentes por formato (Issue #116)
    transformations = {f: {s: 0 for s in TRANSFORMATION_STATES} for f in TRANSFORMATION_FORMATS}
    for t in transformation_rows:
        if t["format"] not in transformations:
            continue
        state = "fallida" if t["status"] == "failed" else t["review_status"]
        if state in transformations[t["format"]]:
            transformations[t["format"]][state] += t["n"]

    reviewed = sum(v for t in decisions.values() for s, v in t.items() if s != "pendiente")
    accepted = sum(t["aceptada"] + t["editada"] for t in decisions.values())
    return {
        "analyses": len(analyses),
        "avg_score": round(sum(scores) / len(scores)) if scores else None,
        "proposals": len(proposals),
        "acceptance_rate": round(accepted / reviewed * 100) if reviewed else None,
        "score_histogram": histogram,
        "by_source": {s: by_source.get(s, 0) for s in SOURCE_TYPES},
        "barriers": barriers,
        "decisions": decisions,
        "transformations": transformations,
        "recent": recent,
    }


def _share_state(row: dict, now_iso: str) -> str:
    if row["revoked_at"]:
        return "revocado"
    file_expires = row["file_expires_at"]
    if row["expires_at"] <= now_iso or (file_expires and file_expires <= now_iso):
        return "vencido"
    return "activo"


def _files(events: list[dict], previous: list[dict], starts: list[datetime], step: timedelta,
           start: datetime, end: datetime, now: datetime) -> dict:
    """Archivos y enlaces del período: eventos de file_events y enlaces creados en file_shares."""
    n = len(starts)
    activity = {k: [0] * n for k in FILE_ACTIVITY}
    uploaded_bytes = [0] * n
    by_format = {f: {k: 0 for k in FILE_KINDS} for f in FILE_FORMATS}
    exports = {d: {v: 0 for v in EXPORT_DELIVERIES} for d in EXPORT_DOCUMENTS}
    for e in events:
        i = _bucket_index(_parse_utc(e["created_at"]), starts, step)
        if i is None:
            continue
        if e["event"] in activity:
            activity[e["event"]][i] += 1
        if e["event"] == "upload":
            uploaded_bytes[i] += e["size_bytes"] or 0
        if e["event"] in ("upload", "export") and e["format"] in by_format:
            by_format[e["format"]][e["event"]] += 1
        if e["event"] == "export" and e["detail"]:
            document, _, delivery = e["detail"].partition(":")
            if document in exports and delivery in exports[document]:
                exports[document][delivery] += 1

    def count(rows: list[dict], event: str) -> int:
        return sum(1 for r in rows if r["event"] == event)

    def total_bytes(rows: list[dict]) -> int:
        return sum(r["size_bytes"] or 0 for r in rows if r["event"] == "upload")

    kpis = {
        "uploads": {"value": count(events, "upload"), "previous": count(previous, "upload"), "spark": activity["upload"]},
        "exports": {"value": count(events, "export"), "previous": count(previous, "export"), "spark": activity["export"]},
        "shares_created": {"value": count(events, "share_created"), "previous": count(previous, "share_created"), "spark": None},
        "share_opens": {"value": count(events, "share_opened"), "previous": count(previous, "share_opened"),
                        "spark": activity["share_opened"]},
        "uploaded_bytes": {"value": total_bytes(events), "previous": total_bytes(previous), "spark": uploaded_bytes},
    }

    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    with get_connection() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT s.created_at, s.expires_at, s.revoked_at, s.access_count, s.last_access_at, "
            "f.kind, f.filename, f.expires_at AS file_expires_at "
            "FROM file_shares s LEFT JOIN files f ON f.id = s.file_id "
            "WHERE s.created_at >= ? AND s.created_at < ? ORDER BY s.created_at DESC",
            (start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")),
        )]
    shares_by_kind = {k: {s: 0 for s in SHARE_STATES} for k in FILE_KINDS}
    recent = []
    for r in rows:
        state = _share_state(r, now_iso)
        if r["kind"] in shares_by_kind:
            shares_by_kind[r["kind"]][state] += 1
        if len(recent) < RECENT_SHARES:
            recent.append({
                "created_at": r["created_at"],
                # Solo la extensión: el nombre del archivo puede tener datos del docente o del curso
                "format": file_event_repository.file_format(r["filename"]),
                "kind": r["kind"],
                "state": state,
                "access_count": r["access_count"],
                "last_access_at": r["last_access_at"],
            })

    return {
        "kpis": kpis,
        "rejected_uploads": count(events, "upload_rejected"),
        "revoked_shares": count(events, "share_revoked"),
        "activity": activity,
        "by_format": by_format,
        "exports": exports,
        "shares": {
            "by_kind": shares_by_kind,
            "created": len(rows),
            "opened": sum(1 for r in rows if r["access_count"] > 0),
            "opens": sum(r["access_count"] for r in rows),
            "recent": recent,
        },
    }


def build_metrics(range_key: str = "24h", tz_offset_minutes: int = 0, now: datetime | None = None) -> dict:
    if range_key not in RANGES:
        raise ValueError(f"Rango inválido. Opciones: {', '.join(RANGES)}.")
    span, unit = RANGES[range_key]
    step = timedelta(hours=1) if unit == "hour" else timedelta(days=1)
    now = now or datetime.now(timezone.utc)
    tz_offset = timedelta(minutes=tz_offset_minutes)

    starts = _bucket_starts(now, span, unit, tz_offset)
    start, end = starts[0], starts[-1] + step
    events = ai_usage_repository.list_between(start, end)
    previous = ai_usage_repository.list_between(start - (end - start), start)
    usage = _usage_block(events, starts, step)
    connections = _connections(events)
    file_events = file_event_repository.list_between(start, end)
    previous_file_events = file_event_repository.list_between(start - (end - start), start)

    return {
        "range": range_key,
        "unit": unit,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "buckets": [s.strftime("%Y-%m-%dT%H:%M:%SZ") for s in starts],
        "kpis": _kpis(events, previous, usage),
        "usage": usage,
        "tts": _tts(events, starts, step),
        "latency_by_model": _latency_by_model(events),
        "connections": connections,
        "alerts": [
            {"label": c["label"], "status": c["status"], "usage_pct": c["usage_pct"]}
            for c in connections
            if c["status"] in ("near-limit", "exhausted")
        ],
        "product": _product(now),
        "files": _files(file_events, previous_file_events, starts, step, start, end, now),
    }
