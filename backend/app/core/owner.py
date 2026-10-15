"""
Dueño de los recursos de un request (archivos de R2, Issue #62).

PROVISIONAL hasta el login de docentes (#60): la API todavía es anónima, así que todos los
archivos quedan a nombre de ANONYMOUS_OWNER (claves users/anon/...). Mientras tanto, el
acceso a un archivo depende de conocer su file_id (UUID aleatorio, no adivinable).

Con #60, esta función pasa a devolver el id de usuario de Clerk del token validado; los
endpoints, la tabla files y las claves en R2 no cambian.
"""
ANONYMOUS_OWNER = "anon"


def current_owner() -> str:
    return ANONYMOUS_OWNER
