"""
Migra los datos del SQLite del backend (volumen de Railway) a Cloudflare D1 (Issue #59).

Se corre en la consola del servicio de Railway, que tiene el volumen y las variables del
Worker (D1_GATEWAY_URL, D1_GATEWAY_TOKEN). Antes hay que aplicar las migraciones del
esquema en D1 (`npm run migrate:remote` en d1-gateway/).

    python scripts/migrate_sqlite_to_d1.py --dry-run            # muestra qué copiaría
    python scripts/migrate_sqlite_to_d1.py                      # copia y verifica conteos
    python scripts/migrate_sqlite_to_d1.py --source /data/app_data.db

- Copia las filas tal cual, incluidos los ids y las API keys cifradas (DB_ENCRYPTION_KEY no
  cambia, así que siguen descifrándose igual).
- Si una tabla de D1 ya tiene filas, no la toca salvo con --force (que reemplaza por id).
- Solo copia las columnas que existen en los dos lados (tolera bases viejas).
- No modifica el SQLite de origen.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.db.d1_client import D1Error, client_from_settings  # noqa: E402

# Orden pensado para que las referencias lógicas queden consistentes
TABLES = ["api_keys", "admin_members", "analyses", "adaptation_proposals", "adaptation_decisions", "ai_usage_events"]
MAX_STATEMENTS_PER_BATCH = 50
MAX_BATCH_BYTES = 800_000


def source_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def target_columns(db, table: str) -> list[str]:
    return [r["name"] for r in db.query(f"PRAGMA table_info({table})").rows]


def chunks(statements: list[tuple[str, list]]):
    batch, size = [], 0
    for sql, params in statements:
        weight = len(sql) + sum(len(str(p)) for p in params)
        if batch and (len(batch) >= MAX_STATEMENTS_PER_BATCH or size + weight > MAX_BATCH_BYTES):
            yield batch
            batch, size = [], 0
        batch.append((sql, params))
        size += weight
    if batch:
        yield batch


def migrate(source: str, db, *, dry_run: bool = False, force: bool = False, out=print) -> dict[str, int]:
    """Copia cada tabla del SQLite `source` a D1. Devuelve {tabla: filas copiadas}."""
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    copied: dict[str, int] = {}
    try:
        existing = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for table in TABLES:
            if table not in existing:
                out(f"- {table}: no existe en el origen, se omite")
                continue
            columns = [c for c in source_columns(src, table) if c in set(target_columns(db, table))]
            rows = src.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
            in_target = db.query(f"SELECT COUNT(*) AS n FROM {table}").rows[0]["n"]
            if in_target and not force:
                raise SystemExit(
                    f"La tabla {table} de D1 ya tiene {in_target} filas. No se migró nada más. "
                    "Revisá que no sea una migración repetida o usá --force para reemplazar por id."
                )
            verb = "INSERT OR REPLACE" if force else "INSERT"
            sql = f"{verb} INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})"
            statements = [(sql, list(row)) for row in rows]
            if dry_run:
                out(f"- {table}: {len(rows)} filas a copiar (columnas: {len(columns)})")
                copied[table] = len(rows)
                continue
            for batch in chunks(statements):
                db.batch(batch)
            total = db.query(f"SELECT COUNT(*) AS n FROM {table}").rows[0]["n"]
            if total < len(rows):
                raise SystemExit(f"{table}: se copiaron {len(rows)} filas pero D1 tiene {total}. Revisá antes de seguir.")
            out(f"- {table}: {len(rows)} filas copiadas (D1 tiene {total})")
            copied[table] = len(rows)
    finally:
        src.close()
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default=settings.DATABASE_PATH, help="SQLite de origen (por defecto DATABASE_PATH)")
    parser.add_argument("--dry-run", action="store_true", help="solo mostrar qué se copiaría")
    parser.add_argument("--force", action="store_true", help="copiar aunque D1 ya tenga filas (reemplaza por id)")
    args = parser.parse_args()

    if not Path(args.source).exists():
        print(f"ERROR: no existe {args.source}")
        return 1
    print(f"Origen: {args.source}\nDestino: {settings.D1_GATEWAY_URL}{' (dry-run)' if args.dry_run else ''}")
    try:
        with client_from_settings() as db:
            migrate(args.source, db, dry_run=args.dry_run, force=args.force)
    except D1Error as e:
        print(f"ERROR: {e}")
        return 1
    print("Listo." if not args.dry_run else "Dry-run: no se escribió nada en D1.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
