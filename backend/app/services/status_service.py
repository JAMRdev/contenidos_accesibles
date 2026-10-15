"""
Estado de los servicios conectados para la sección Estado de /admin.

Pensado para no cargar Railway ni guardar historial:
- Cada servicio se consulta solo cuando alguien tiene la pestaña abierta (el panel pide el
  estado cada 30 s mientras está visible). No hay tareas en segundo plano.
- El resultado se reutiliza durante CACHE_TTL_S: varios paneles abiertos comparten la misma
  ronda de chequeos.
- Lo único que se recuerda, en memoria, es desde cuándo cada servicio está en su estado
  actual (se pierde con cada deploy).
- Los chequeos son livianos: un GET a una URL pública, un `SELECT 1` en la base y el estado
  de las conexiones de IA que ya tiene el backend. A los proveedores de IA no se les manda
  nada, para no gastar cuota.

Para sumar un servicio nuevo: escribir su función de chequeo (recibe el cliente HTTP y
devuelve un Check) y agregarla a `_registry()` con su nombre, dónde corre y qué hace.
Si tiene URL pública, sumarla también a `links()`.
"""
import asyncio
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, Literal
from urllib.parse import urlsplit

import httpx

from app.core.config import settings

State = Literal["ok", "degraded", "down", "unconfigured"]

CACHE_TTL_S = 20
TIMEOUT_S = 5.0
# Por encima de esto un servicio que responde se marca como lento
SLOW_MS = 1500
_SEVERITY: dict[str, int] = {"unconfigured": 0, "ok": 1, "degraded": 2, "down": 3}
_STARTED_AT = time.time()
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


@dataclass
class Check:
    state: State
    detail: str
    latency_ms: int | None = None
    url: str | None = None
    facts: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class Service:
    id: str
    name: str
    platform: str
    description: str
    check: Callable[[httpx.AsyncClient], Awaitable[Check]]


# ---------------------------------------------------------------------------
# Chequeos
# ---------------------------------------------------------------------------

def _elapsed_ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


def _by_latency(latency_ms: int, detail: str, **kwargs) -> Check:
    if latency_ms > SLOW_MS:
        return Check("degraded", f"{detail} · respondió lento", latency_ms, **kwargs)
    return Check("ok", detail, latency_ms, **kwargs)


async def _http_check(client: httpx.AsyncClient, url: str, *, expect_json_ok: bool = False) -> Check:
    """GET a una URL pública: caído si no responde o devuelve 5xx; con problemas si devuelve 4xx."""
    start = time.perf_counter()
    try:
        response = await client.get(url)
    except httpx.TimeoutException:
        return Check("down", f"No respondió en {TIMEOUT_S:g} s", url=url)
    except httpx.HTTPError as e:
        return Check("down", f"No se pudo conectar ({e.__class__.__name__})", url=url)
    latency = _elapsed_ms(start)
    code = response.status_code
    if code >= 500:
        return Check("down", f"Respondió {code}", latency, url=url)
    if code >= 400:
        return Check("degraded", f"Respondió {code}", latency, url=url)
    if expect_json_ok:
        try:
            ok = response.json().get("ok") is True
        except ValueError:
            ok = False
        if not ok:
            return Check("degraded", "Respondió, pero sin el { ok: true } esperado", latency, url=url)
    return _by_latency(latency, f"Respondió {code}", url=url)


def _uptime(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} h {minutes} min"
    return f"{hours // 24} días"


async def check_api(_client: httpx.AsyncClient) -> Check:
    """El propio backend: si esta función corre, está en línea. Datos del deploy de Railway si los hay."""
    facts = [("Versión", settings.VERSION), ("Entorno", settings.ENVIRONMENT),
             ("En marcha hace", _uptime(time.time() - _STARTED_AT))]
    if region := os.getenv("RAILWAY_REPLICA_REGION"):
        facts.append(("Región", region))
    if commit := os.getenv("RAILWAY_GIT_COMMIT_SHA"):
        facts.append(("Commit", commit[:7]))
    return Check("ok", "En línea", facts=facts)


def _select_one() -> None:
    from app.db.database import get_connection

    with get_connection() as conn:
        conn.execute("SELECT 1").fetchone()


async def check_database(_client: httpx.AsyncClient) -> Check:
    from app.db.database import using_d1

    facts = [("Acceso", "Vía Worker d1-gateway" if using_d1() else "Archivo local")]
    start = time.perf_counter()
    try:
        await asyncio.to_thread(_select_one)
    except Exception as e:  # noqa: BLE001 - cualquier falla de la base es "caída"
        return Check("down", f"La consulta de prueba falló ({e.__class__.__name__})", facts=facts)
    return _by_latency(_elapsed_ms(start), "Consulta de prueba OK", facts=facts)


async def check_d1_gateway(client: httpx.AsyncClient) -> Check:
    if not settings.D1_GATEWAY_URL:
        return Check("unconfigured", "Sin D1_GATEWAY_URL: la base es SQLite local")
    return await _http_check(client, f"{settings.D1_GATEWAY_URL.rstrip('/')}/health", expect_json_ok=True)


async def check_storage(_client: httpx.AsyncClient) -> Check:
    """Archivos de los docentes: HEAD del bucket de R2, o escritura de prueba en la carpeta local."""
    from app.services import storage_service

    if settings.STORAGE_BACKEND == "r2":
        facts = [("Bucket", settings.R2_BUCKET or "—")]
    else:
        facts = [("Carpeta", settings.LOCAL_STORAGE_PATH)]
    start = time.perf_counter()
    try:
        await asyncio.to_thread(storage_service.get_storage().ping)
    except Exception as e:  # noqa: BLE001 - cualquier falla del almacenamiento es "caída"
        return Check("down", f"No responde ({e.__class__.__name__})", facts=facts)
    latency = _elapsed_ms(start)
    if settings.STORAGE_BACKEND == "local" and settings.ENVIRONMENT != "development":
        # En Railway el disco del contenedor se borra en cada deploy
        return Check("degraded", "Disco local fuera de desarrollo: los archivos se pierden en cada deploy", latency, facts=facts)
    return _by_latency(latency, "Bucket accesible" if settings.STORAGE_BACKEND == "r2" else "Carpeta local accesible", facts=facts)


async def check_ai(_client: httpx.AsyncClient) -> Check:
    """Estado de las conexiones de IA según el propio backend (no consulta a los proveedores)."""
    from app.services import key_rotation_service

    rows = await asyncio.to_thread(key_rotation_service.list_status_for_dashboard)
    if not rows:
        return Check("unconfigured", "No hay conexiones de IA cargadas")
    usable = [r for r in rows if r["status"] in ("active", "standby", "near-limit")]
    current = next((r for r in rows if r["status"] in ("active", "near-limit")), usable[0] if usable else None)
    facts = [("Disponibles", f"{len(usable)} de {len(rows)}")]
    if current is None:
        return Check("down", "Todas las conexiones están agotadas o deshabilitadas", facts=facts)
    facts.insert(0, ("En uso", f"{current['label']} · {current['model']}"))
    if current["status"] == "near-limit" or len(usable) < len([r for r in rows if r["enabled"]]):
        return Check("degraded", "Hay conexiones cerca del límite o agotadas", facts=facts)
    return Check("ok", "Hay una conexión disponible", facts=facts)


async def check_clerk(client: httpx.AsyncClient) -> Check:
    from app.admin import clerk_auth

    domain = clerk_auth.frontend_api(settings.ADMIN_CLERK_PUBLISHABLE_KEY) if settings.admin_clerk_enabled else None
    if not domain:
        return Check("unconfigured", "Login con Clerk desactivado: solo usuario y contraseña del .env")
    return await _http_check(client, f"https://{domain}/.well-known/jwks.json")


async def check_test_web(client: httpx.AsyncClient) -> Check:
    if not settings.TEST_WEB_URL:
        return Check("unconfigured", "Sin TEST_WEB_URL")
    return await _http_check(client, settings.TEST_WEB_URL.strip())


def frontend_origins() -> list[str]:
    """FRONTEND_URL más los orígenes de CORS que no son locales ni la web de pruebas."""
    test_web = settings.test_web_origin
    candidates = ([settings.FRONTEND_URL.strip()] if settings.FRONTEND_URL else []) + list(settings.BACKEND_CORS_ORIGINS)
    origins: list[str] = []
    for o in candidates:
        o = o.rstrip("/")
        if o and urlsplit(o).hostname not in _LOCAL_HOSTS and o != test_web and o not in origins:
            origins.append(o)
    return origins


async def check_frontend(client: httpx.AsyncClient) -> Check:
    origins = frontend_origins()
    if not origins:
        return Check("unconfigured", "Sin FRONTEND_URL ni orígenes desplegados en BACKEND_CORS_ORIGINS")
    checks = await asyncio.gather(*(_http_check(client, o) for o in origins))
    worst = max(checks, key=lambda c: _SEVERITY[c.state])
    if len(origins) == 1:
        return worst
    worst.detail = f"{len(origins)} orígenes · el peor: {worst.detail}"
    return worst


_PLATFORMS = {"vercel.app": "Vercel", "onrender.com": "Render", "netlify.app": "Netlify",
              "pages.dev": "Cloudflare Pages", "github.io": "GitHub Pages", "up.railway.app": "Railway"}


def _platform(url: str | None, default: str = "Web") -> str:
    host = urlsplit(url or "").hostname or ""
    return next((name for suffix, name in _PLATFORMS.items() if host.endswith(suffix)), default)


def _registry() -> list[Service]:
    """Servicios que muestra la sección Estado, en este orden."""
    return [
        Service("api", "API del backend", "Railway" if os.getenv("RAILWAY_ENVIRONMENT") else "Local",
"FastAPI: análisis, adaptaciones y este panel.", check_api),
        Service("database", "Base de datos", "Cloudflare D1" if settings.DATABASE_BACKEND == "d1" else "SQLite",
                "API keys cifradas, diagnósticos, uso de la IA y usuarios del panel.", check_database),
        Service("d1-gateway", "Worker d1-gateway", "Cloudflare Workers",
                "Pasarela entre el backend y D1 (/health, sin token).", check_d1_gateway),
        Service("storage", "Archivos", "Cloudflare R2" if settings.STORAGE_BACKEND == "r2" else "Disco local",
                "Archivos de los docentes y exportaciones (subida directa con URL firmada).", check_storage),
        Service("ai", "Conexiones de IA", "Proveedores de IA",
                "Según el estado de rotación del backend; no gasta cuota.", check_ai),
        Service("clerk", "Login con Clerk", "Clerk", "Login del panel y de /docs con GitHub.", check_clerk),
        Service("test-web", "Web de pruebas", _platform(settings.TEST_WEB_URL),
                "Web estática para probar la API.", check_test_web),
        Service("frontend", "Frontend", _platform(next(iter(frontend_origins()), None)),
                "Aplicación para docentes.", check_frontend),
    ]


# ---------------------------------------------------------------------------
# Ronda de chequeos con caché
# ---------------------------------------------------------------------------

# Los tests lo reemplazan por un httpx.MockTransport (sin red)
_transport: httpx.AsyncBaseTransport | None = None
_cache: tuple[float, dict] | None = None
_cache_lock = asyncio.Lock()
# id del servicio → (estado, desde cuándo)
_since: dict[str, tuple[str, str]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _run(service: Service, client: httpx.AsyncClient) -> Check:
    try:
        return await asyncio.wait_for(service.check(client), timeout=TIMEOUT_S + 1)
    except asyncio.TimeoutError:
        return Check("down", f"El chequeo no terminó en {TIMEOUT_S:g} s")
    except Exception as e:  # noqa: BLE001 - un chequeo roto no tiene que tirar la sección
        return Check("down", f"El chequeo falló ({e.__class__.__name__})")


async def _collect() -> dict:
    registry = _registry()
    async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True, transport=_transport,
                                 headers={"User-Agent": "contenidos-accesibles-status/1"}) as client:
        checks = await asyncio.gather(*(_run(s, client) for s in registry))
    checked_at = _now_iso()
    services = []
    for service, check in zip(registry, checks):
        previous = _since.get(service.id)
        if previous is None or previous[0] != check.state:
            _since[service.id] = (check.state, checked_at)
        item = asdict(check)
        item["facts"] = [{"label": k, "value": v} for k, v in check.facts]
        services.append({
            "id": service.id, "name": service.name, "platform": service.platform,
            "description": service.description, "since": _since[service.id][1], **item,
        })
    active = [s for s in services if s["state"] != "unconfigured"]
    overall = max((s["state"] for s in active), key=_SEVERITY.__getitem__, default="unconfigured")
    return {
        "checked_at": checked_at,
        "ttl_s": CACHE_TTL_S,
        "overall": overall,
        "counts": {state: sum(s["state"] == state for s in services) for state in _SEVERITY},
        "services": services,
    }


async def get_status() -> dict:
    """Estado de todos los servicios; reutiliza la última ronda si tiene menos de CACHE_TTL_S."""
    global _cache
    async with _cache_lock:
        if _cache and time.monotonic() - _cache[0] < CACHE_TTL_S:
            return _cache[1]
        payload = await _collect()
        _cache = (time.monotonic(), payload)
        return payload


def links(base_url: str, panel_prefix: str) -> list[dict]:
    """URLs del proyecto (sin secretos). `base_url` es el origen con el que se abrió el panel."""
    base = base_url.rstrip("/")
    items = [
        {"group": "API", "label": "API (raíz)", "url": f"{base}/"},
        {"group": "API", "label": "Health", "url": f"{base}{settings.API_V1_STR}/health"},
        {"group": "API", "label": "Swagger (/docs)", "url": f"{base}/docs"},
        {"group": "API", "label": "ReDoc (/redoc)", "url": f"{base}/redoc"},
        {"group": "API", "label": "Panel", "url": f"{base}{panel_prefix}"},
    ]
    if settings.D1_GATEWAY_URL:
        items.append({"group": "Cloudflare", "label": "Worker d1-gateway (/health)",
                      "url": f"{settings.D1_GATEWAY_URL.rstrip('/')}/health"})
    if settings.TEST_WEB_URL:
        items.append({"group": "Web", "label": "Web de pruebas", "url": settings.TEST_WEB_URL.strip()})
    for origin in frontend_origins():
        items.append({"group": "Web", "label": "Frontend", "url": origin})
    return items
