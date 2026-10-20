"""
Dueño de los recursos de un request (archivos de R2, Issue #62; por usuario desde el Issue #61).

Es el id de usuario de Clerk del token del docente, el de la persona logueada en la web de
pruebas (test-web_<tipo>_<usuario>) o ANONYMOUS_OWNER mientras la API es anónima (sin
USERS_CLERK_JWT_KEY). Ver app/core/teacher_auth.py.
"""
from fastapi import Depends

from app.core.permissions import ANONYMOUS_OWNER, Caller
from app.core.teacher_auth import current_caller

__all__ = ["ANONYMOUS_OWNER", "current_owner"]


def current_owner(caller: Caller = Depends(current_caller)) -> str:
    return caller.user_id
