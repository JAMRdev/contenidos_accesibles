"""
D1 de prueba: responde como el Worker pasarela (query / batch) pero guarda los datos en un
SQLite local. Sirve para correr la suite contra el adaptador de D1 sin red:

    TEST_DATABASE_BACKEND=d1 pytest

- La base sale de settings.DATABASE_PATH en cada llamada (los tests la cambian con monkeypatch).
- El esquema se crea aplicando las migraciones de d1-gateway/migrations (así también se validan).
- batch es atómico, como en D1: si una sentencia falla, no se aplica ninguna.
"""
import sqlite3
from pathlib import Path

from app.core.config import settings
from app.db.d1_client import D1QueryError, D1Result

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "d1-gateway" / "migrations"
_migrated: set[str] = set()


def apply_migrations(path: str) -> None:
    conn = sqlite3.connect(path)
    try:
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.executescript(migration.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


class FakeD1Client:
    def __init__(self):
        self.queries = 0
        self.batches = 0

    def _connect(self) -> sqlite3.Connection:
        path = settings.DATABASE_PATH
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        if path not in _migrated:
            apply_migrations(path)
            _migrated.add(path)
        conn = sqlite3.connect(path, isolation_level=None)  # BEGIN/COMMIT a mano
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _run(conn: sqlite3.Connection, sql: str, params) -> D1Result:
        if sql.strip().rstrip(";").count(";"):
            raise D1QueryError("Se admite una sola sentencia por elemento (usá /batch para varias).")
        try:
            cur = conn.execute(sql, list(params))
            rows = [dict(r) for r in cur.fetchall()]
        except sqlite3.Error as e:
            raise D1QueryError(f"D1_ERROR: {e}") from e
        return D1Result(rows=rows, meta={"changes": cur.rowcount if cur.rowcount > 0 else 0, "last_row_id": cur.lastrowid})

    def query(self, sql, params=()) -> D1Result:
        self.queries += 1
        conn = self._connect()
        try:
            return self._run(conn, sql, params)
        finally:
            conn.close()

    def batch(self, statements) -> list[D1Result]:
        self.batches += 1
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            try:
                results = [self._run(conn, sql, params) for sql, params in statements]
            except Exception:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            return results
        finally:
            conn.close()
