"""Usuarios de GitHub con acceso al panel /adminv2 y su rol ('admin' o 'viewer')."""
import re

from app.db.database import get_connection

ROLES = ("admin", "viewer")

# Reglas de GitHub: letras, números y guiones simples, sin guion al inicio ni al final, hasta 39 caracteres
_GITHUB_USERNAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")

_COLUMNS = (
    "id, github_username, role, enabled, display_name, avatar_url, clerk_user_id, "
    "last_login_at, added_by, created_at, updated_at"
)


def normalize_username(raw: str) -> str:
    username = raw.strip().lstrip("@")
    if not _GITHUB_USERNAME.match(username):
        raise ValueError(f"'{raw}' no es un usuario de GitHub válido.")
    return username


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"Rol inválido: {role!r}. Opciones: {', '.join(ROLES)}.")


def _row(row) -> dict:
    data = dict(row)
    data["enabled"] = bool(data["enabled"])
    return data


def list_all() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM admin_members ORDER BY role, github_username COLLATE NOCASE"
        ).fetchall()
    return [_row(r) for r in rows]


def get(member_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(f"SELECT {_COLUMNS} FROM admin_members WHERE id = ?", (member_id,)).fetchone()
    return _row(row) if row else None


def get_by_username(github_username: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM admin_members WHERE github_username = ?", (github_username,)
        ).fetchone()
    return _row(row) if row else None


def add(github_username: str, role: str, added_by: str | None = None) -> dict:
    username = normalize_username(github_username)
    _check_role(role)
    if get_by_username(username):
        raise ValueError(f"{username} ya tiene acceso al panel.")
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO admin_members (github_username, role, added_by) VALUES (?, ?, ?)",
            (username, role, added_by),
        )
    return get_by_username(username)


def seed(members: dict[str, str]) -> int:
    """Agrega los usuarios que falten (no cambia los existentes). Devuelve cuántos agregó."""
    added = 0
    for username, role in members.items():
        try:
            if not get_by_username(normalize_username(username)):
                add(username, role, added_by=".env")
                added += 1
        except ValueError:
            continue
    return added


def set_role(member_id: int, role: str) -> None:
    _check_role(role)
    with get_connection() as conn:
        conn.execute(
            "UPDATE admin_members SET role = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
            (role, member_id),
        )


def set_enabled(member_id: int, enabled: bool) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE admin_members SET enabled = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
            (int(enabled), member_id),
        )


def delete(member_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM admin_members WHERE id = ?", (member_id,))


def record_login(member_id: int, *, display_name: str | None, avatar_url: str | None, clerk_user_id: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE admin_members
            SET display_name = ?, avatar_url = ?, clerk_user_id = ?,
                last_login_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            WHERE id = ?
            """,
            (display_name, avatar_url, clerk_user_id, member_id),
        )


def count_enabled_admins() -> int:
    with get_connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM admin_members WHERE role = 'admin' AND enabled = 1"
        ).fetchone()[0]
