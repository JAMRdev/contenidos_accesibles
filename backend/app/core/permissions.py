"""
Roles y permisos de la API (Issue #61).

- docente: analiza, adapta, sube, exporta y comparte sus propios archivos y diagnósticos.
- admin: además configura la retención de archivos (#64) y administra usuarios.

El rol viaja en el token de sesión de Clerk (claim `role`, desde publicMetadata), así el backend
no consulta a Clerk en cada request. Sin claim, o con un valor desconocido, el rol es docente:
ese es el rol por defecto al registrarse. Los permisos se derivan del rol acá, en código.

La dependencia que exige un permiso es require_permission (app/core/teacher_auth.py). El panel
/admin tiene sus propios roles (admin y solo lectura, app/admin/session.py).
"""
from dataclasses import dataclass

DOCENTE = "docente"
ADMIN = "admin"

# Dueño de los recursos mientras la API es anónima (sin USERS_CLERK_JWT_KEY)
ANONYMOUS_OWNER = "anon"

_DOCENTE_PERMISSIONS = frozenset({
    "analysis:create",
    "adaptations:manage",
    "files:manage",
    "files:share",
    "export:create",
})

PERMISSIONS: dict[str, frozenset[str]] = {
    DOCENTE: _DOCENTE_PERMISSIONS,
    ADMIN: _DOCENTE_PERMISSIONS | {"settings:retention", "users:manage"},
}


def role_from_claim(value) -> str:
    """Rol del claim `role` del token; docente si falta o no es uno conocido."""
    return value if isinstance(value, str) and value in PERMISSIONS else DOCENTE


@dataclass(frozen=True)
class Caller:
    """Quién hace el request a la API: dueño de los recursos que crea y su rol."""

    user_id: str
    role: str = DOCENTE
    authenticated: bool = False

    def can(self, permission: str) -> bool:
        return permission in PERMISSIONS.get(self.role, frozenset())
