import sqlite3
from datetime import datetime, timezone

from app.core.permissions import ANONYMOUS_OWNER
from app.db.database import get_connection


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "analysis_id": row["analysis_id"],
        "barrier_id": row["barrier_id"],
        "status": row["status"],
        "proposal_json": row["proposal_json"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "user_id": row["user_id"],
    }


_table_initialized = False


def _ensure_table(conn: sqlite3.Connection) -> None:
    global _table_initialized
    if getattr(conn, "is_d1", False):
        return  # en D1 las tablas vienen de las migraciones de wrangler
    if not _table_initialized:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS adaptation_proposals (
                id                      TEXT PRIMARY KEY,
                analysis_id             TEXT NOT NULL,
                barrier_id              TEXT NOT NULL,
                status                  TEXT NOT NULL DEFAULT 'pendiente',
                proposal_json           TEXT NOT NULL,
                created_at              TEXT NOT NULL,
                expires_at              TEXT NOT NULL,
                user_id                 TEXT NOT NULL DEFAULT 'anon'
            );
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_adaptation_proposals_analysis ON adaptation_proposals(analysis_id);"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_adaptation_proposals_expires_at ON adaptation_proposals(expires_at);"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS adaptation_decisions (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                proposal_id             TEXT NOT NULL,
                analysis_id             TEXT NOT NULL,
                barrier_id              TEXT NOT NULL,
                adaptation_type         TEXT NOT NULL,
                action                  TEXT NOT NULL,
                from_status             TEXT NOT NULL,
                to_status               TEXT NOT NULL,
                edited_text             TEXT,
                created_at              TEXT NOT NULL,
                expires_at              TEXT NOT NULL,
                user_id                 TEXT NOT NULL DEFAULT 'anon'
            );
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_adaptation_decisions_analysis ON adaptation_decisions(analysis_id);"
        )
        _table_initialized = True


_COLUMNS = "id, analysis_id, barrier_id, status, proposal_json, created_at, expires_at, user_id"


def save(
    proposal_id: str,
    analysis_id: str,
    barrier_id: str,
    status: str,
    proposal_json: str,
    created_at: str,
    expires_at: str,
    user_id: str = ANONYMOUS_OWNER,
) -> None:
    """Guarda o actualiza una propuesta de adaptación, a nombre del dueño del diagnóstico (Issue #61)."""
    with get_connection() as conn:
        _ensure_table(conn)
        conn.execute(
            f"INSERT OR REPLACE INTO adaptation_proposals ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (proposal_id, analysis_id, barrier_id, status, proposal_json, created_at, expires_at, user_id),
        )


def get(proposal_id: str) -> dict | None:
    """Obtiene una propuesta por ID sin verificar expiración ni dueño."""
    with get_connection() as conn:
        _ensure_table(conn)
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM adaptation_proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None


def list_by_analysis(analysis_id: str) -> list[dict]:
    """Propuestas de un diagnóstico, de la más antigua a la más reciente."""
    with get_connection() as conn:
        _ensure_table(conn)
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM adaptation_proposals WHERE analysis_id = ? ORDER BY created_at, rowid",
            (analysis_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def find_pending(analysis_id: str, barrier_id: str, *, exclude_id: str | None = None) -> str | None:
    """Id de la propuesta 'pendiente' vigente de esa barrera (como mucho hay una), o None."""
    with get_connection() as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT id FROM adaptation_proposals WHERE analysis_id = ? AND barrier_id = ? AND status = 'pendiente' "
            "AND expires_at > ? AND id != ? ORDER BY created_at LIMIT 1",
            (analysis_id, barrier_id, _now_utc_iso(), exclude_id or ""),
        ).fetchone()
        return row["id"] if row else None


def transition(
    proposal_id: str,
    from_status: str,
    to_status: str,
    proposal_json: str,
    log_entry: dict,
) -> bool:
    """
    Cambia el estado de una propuesta y registra la modificación en una única transacción.

    Solo actualiza si el estado sigue siendo from_status: si otra request la decidió
    en el medio, devuelve False y no registra nada.
    """
    with get_connection() as conn:
        _ensure_table(conn)
        cur = conn.execute(
            "UPDATE adaptation_proposals SET status = ?, proposal_json = ? WHERE id = ? AND status = ?",
            (to_status, proposal_json, proposal_id, from_status),
        )
        if cur.rowcount == 0:
            return False
        conn.execute(
            """
            INSERT INTO adaptation_decisions
                (proposal_id, analysis_id, barrier_id, user_id, adaptation_type, action,
                 from_status, to_status, edited_text, created_at, expires_at)
            VALUES (:proposal_id, :analysis_id, :barrier_id, :user_id, :adaptation_type, :action,
                    :from_status, :to_status, :edited_text, :created_at, :expires_at)
            """,
            log_entry,
        )
        return True


def list_decisions(analysis_id: str) -> list[dict]:
    """Registro de modificaciones de un diagnóstico, en orden cronológico."""
    with get_connection() as conn:
        _ensure_table(conn)
        rows = conn.execute(
            """
            SELECT proposal_id, barrier_id, adaptation_type, action, from_status, to_status,
                   edited_text, created_at
            FROM adaptation_decisions
            WHERE analysis_id = ?
            ORDER BY id
            """,
            (analysis_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_expired(now_iso: str | None = None) -> int:
    """Elimina las propuestas vencidas y su registro. Retorna la cantidad de propuestas eliminadas."""
    if now_iso is None:
        now_iso = _now_utc_iso()

    with get_connection() as conn:
        _ensure_table(conn)
        conn.execute("DELETE FROM adaptation_decisions WHERE expires_at < ?", (now_iso,))
        cur = conn.execute("DELETE FROM adaptation_proposals WHERE expires_at < ?", (now_iso,))
        return cur.rowcount
