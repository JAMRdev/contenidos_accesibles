"""
Métricas del panel de administración (/admin).

Junta en un solo JSON lo que muestran los gráficos y tablas del panel:
- Uso de la IA a lo largo del tiempo (tabla ai_usage_events).
- Estado y consumo de cada conexión (tabla api_keys).
- Uso del producto en las últimas horas (diagnósticos y propuestas vigentes).

Nunca devuelve el material analizado, los textos propuestos ni las API keys.
"""
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from app.db import ai_usage_repository
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
RECENT_ANALYSES = 15


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
        "recent": recent,
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

    return {
        "range": range_key,
        "unit": unit,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "buckets": [s.strftime("%Y-%m-%dT%H:%M:%SZ") for s in starts],
        "kpis": _kpis(events, previous, usage),
        "usage": usage,
        "latency_by_model": _latency_by_model(events),
        "connections": connections,
        "alerts": [
            {"label": c["label"], "status": c["status"], "usage_pct": c["usage_pct"]}
            for c in connections
            if c["status"] in ("near-limit", "exhausted")
        ],
        "product": _product(now),
    }
