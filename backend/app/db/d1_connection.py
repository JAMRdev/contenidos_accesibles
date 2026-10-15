"""
Conexión a Cloudflare D1 con la misma interfaz que usa el backend de sqlite3 (Issue #59).

Los repositorios hacen `with get_connection() as conn: conn.execute(...)` y usan
`fetchone`, `fetchall`, `rowcount`, `lastrowid` y filas con acceso por nombre. Con
DATABASE_BACKEND=d1, `get_connection` devuelve un `D1Connection` que se comporta igual:

- Las lecturas (SELECT, WITH, PRAGMA) se ejecutan al momento.
- Las escrituras se acumulan y al salir del bloque se mandan juntas en **un batch
  atómico** (D1 no admite BEGIN/COMMIT desde el cliente): si algo falla antes, no se
  aplica ninguna. Equivale a la transacción de sqlite.
- Si el código necesita el resultado de una escritura (`rowcount`, `lastrowid`) o hace
  una lectura después de escribir, primero se mandan las escrituras pendientes. Lo que
  ya se mandó no se puede deshacer: es el único caso en que el bloque no es atómico.
- Los parámetros con nombre (`:nombre` + dict) se pasan a posicionales: D1 solo admite `?`.
"""
import re
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from app.db.d1_client import D1Client, D1Result

_READ_PREFIXES = ("SELECT", "WITH", "PRAGMA", "EXPLAIN")
_NAMED_PARAM = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")


def _is_read(sql: str) -> bool:
    return sql.lstrip().split(None, 1)[0].upper().startswith(_READ_PREFIXES) if sql.strip() else False


def to_positional(sql: str, params: Sequence[Any] | Mapping[str, Any] | None) -> tuple[str, list]:
    """`:nombre` + dict → `?` + lista en el orden en que aparecen. Las listas pasan igual."""
    if params is None:
        return sql, []
    if not isinstance(params, Mapping):
        return sql, list(params)
    ordered: list = []

    def replace(match: re.Match) -> str:
        ordered.append(params[match.group(1)])
        return "?"

    return _NAMED_PARAM.sub(replace, sql), ordered


class D1Row(dict):
    """Fila con acceso por nombre (`row["col"]`) y por posición (`row[0]`), como sqlite3.Row."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class D1Cursor:
    def __init__(self, connection: "D1Connection", result: D1Result | None = None, statement_id: int | None = None):
        self._connection = connection
        self._result = result
        self._statement_id = statement_id

    def _resolve(self) -> D1Result:
        if self._result is None:
            self._result = self._connection._result_of(self._statement_id)
        return self._result

    @property
    def rowcount(self) -> int:
        return self._resolve().changes

    @property
    def lastrowid(self) -> int | None:
        return self._resolve().last_row_id

    def fetchall(self) -> list[D1Row]:
        return [D1Row(r) for r in self._resolve().rows]

    def fetchone(self) -> D1Row | None:
        rows = self._resolve().rows
        return D1Row(rows[0]) if rows else None

    def __iter__(self) -> Iterator[D1Row]:
        return iter(self.fetchall())


class D1Connection:
    """Conexión de una unidad de trabajo (un bloque `with get_connection()`)."""

    is_d1 = True

    def __init__(self, client: D1Client):
        self._client = client
        self._pending: list[tuple[int, str, list]] = []  # (id de sentencia, sql, parámetros)
        self._results: dict[int, D1Result] = {}
        self._next_id = 0

    def execute(self, sql: str, params: Sequence[Any] | Mapping[str, Any] | None = None) -> D1Cursor:
        sql, values = to_positional(sql, params)
        if _is_read(sql):
            self._flush()
            return D1Cursor(self, self._client.query(sql, values))
        statement_id = self._next_id
        self._next_id += 1
        self._pending.append((statement_id, sql, values))
        return D1Cursor(self, statement_id=statement_id)

    def _flush(self) -> None:
        """Manda las escrituras pendientes en un batch y guarda el resultado de cada una."""
        if not self._pending:
            return
        pending, self._pending = self._pending, []
        results = self._client.batch([(sql, values) for _, sql, values in pending])
        for (statement_id, _, _), result in zip(pending, results, strict=True):
            self._results[statement_id] = result

    def _result_of(self, statement_id: int) -> D1Result:
        if statement_id not in self._results:
            self._flush()
        return self._results[statement_id]

    def commit(self) -> None:
        self._flush()

    def rollback(self) -> None:
        self._pending = []  # lo que no se mandó se descarta

    def close(self) -> None:
        self._pending = []

    def executescript(self, script: str) -> None:
        raise NotImplementedError("En D1 el esquema se aplica con migraciones de wrangler (d1-gateway/migrations).")
