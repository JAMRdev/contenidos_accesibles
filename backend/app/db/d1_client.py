"""
Cliente del Worker pasarela de Cloudflare D1 (backend/d1-gateway, Issue #58).

El backend corre en Railway, fuera de Cloudflare: no puede usar el binding de D1 y la
API REST de D1 comparte el límite general de la API de Cloudflare. Por eso habla con
un Worker propio que sí tiene el binding.

- `query(sql, params)`: una sentencia parametrizada.
- `batch([(sql, params), ...])`: varias sentencias atómicas (D1 no admite BEGIN/COMMIT
  desde el cliente: si una falla, no se aplica ninguna).

Siempre con `?` y parámetros: los datos nunca se concatenan al SQL.
Reintenta solo errores transitorios (red, timeout, 502/503/504); nunca un 4xx.
"""
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import settings

Param = str | int | float | bool | None
RETRY_STATUS = {502, 503, 504}


class D1Error(Exception):
    """Falla al consultar D1 a través del Worker."""


class D1AuthError(D1Error):
    """El Worker rechazó el token (401): revisar D1_GATEWAY_TOKEN."""


class D1QueryError(D1Error):
    """La sentencia es inválida (400) o D1 la rechazó (422: sintaxis, restricciones)."""


@dataclass
class D1Result:
    rows: list[dict[str, Any]]
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def changes(self) -> int:
        return int(self.meta.get("changes") or 0)

    @property
    def last_row_id(self) -> int | None:
        return self.meta.get("last_row_id")


def _statement(sql: str, params: Sequence[Param]) -> dict:
    return {"sql": sql, "params": list(params)}


def _result(data: dict) -> D1Result:
    return D1Result(rows=data.get("results") or [], meta=data.get("meta") or {})


class D1Client:
    def __init__(
        self,
        url: str,
        token: str,
        *,
        timeout_s: float = 10.0,
        retries: int = 2,
        backoff_s: float = 0.3,
        transport: httpx.BaseTransport | None = None,
    ):
        self._retries = retries
        self._backoff_s = backoff_s
        self._http = httpx.Client(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=timeout_s,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "D1Client":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _post(self, path: str, payload: dict) -> dict:
        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                response = self._http.post(path, json=payload)
            except httpx.TransportError as e:  # sin conexión, timeout, DNS
                last_error = D1Error(f"No se pudo conectar con el Worker de D1: {e.__class__.__name__}.")
            else:
                if response.status_code in RETRY_STATUS:
                    last_error = D1Error(f"El Worker de D1 respondió {response.status_code}.")
                else:
                    return self._parse(response)
            if attempt < self._retries:
                time.sleep(self._backoff_s * (2 ** attempt))
        raise last_error

    @staticmethod
    def _parse(response: httpx.Response) -> dict:
        try:
            data = response.json()
        except ValueError:
            data = {}
        detail = data.get("error") or response.text[:200]
        if response.status_code == 401:
            raise D1AuthError("El Worker de D1 rechazó el token: revisá D1_GATEWAY_TOKEN.")
        if response.status_code in (400, 422):
            raise D1QueryError(detail)
        if response.status_code != 200:
            raise D1Error(f"El Worker de D1 respondió {response.status_code}: {detail}")
        return data

    def query(self, sql: str, params: Sequence[Param] = ()) -> D1Result:
        return _result(self._post("/query", _statement(sql, params)))

    def batch(self, statements: Sequence[tuple[str, Sequence[Param]]]) -> list[D1Result]:
        if not statements:
            return []
        data = self._post("/batch", {"statements": [_statement(sql, params) for sql, params in statements]})
        return [_result(r) for r in data.get("results") or []]


def client_from_settings(**kwargs) -> D1Client:
    """Cliente con D1_GATEWAY_URL / D1_GATEWAY_TOKEN del .env (o las variables de Railway)."""
    if not settings.D1_GATEWAY_URL or not settings.D1_GATEWAY_TOKEN:
        raise D1Error("Faltan D1_GATEWAY_URL y/o D1_GATEWAY_TOKEN.")
    return D1Client(
        settings.D1_GATEWAY_URL,
        settings.D1_GATEWAY_TOKEN.get_secret_value(),
        timeout_s=settings.D1_GATEWAY_TIMEOUT_S,
        **kwargs,
    )
