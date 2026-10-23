from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import JSONResponse, RedirectResponse

from app.admin import session

LOGIN_URL = "/admin/login"
# Mismo logo que el panel /admin (app/admin/static/favicon.svg)
FAVICON_URL = "/admin/static/favicon.svg"


def setup_protected_docs(app: FastAPI, openapi_url: str) -> None:
    """
    Registra /docs, /redoc y openapi.json detrás de la sesión del panel de administración
    (Clerk o el usuario y la contraseña del .env; admin y solo lectura pueden verla).

    Se usa en staging/production, donde la app se crea con docs_url, redoc_url y
    openapi_url en None para que FastAPI no exponga sus rutas públicas. Sin sesión,
    las páginas llevan al login y vuelven solas después de entrar.
    """

    def _login(request: Request) -> RedirectResponse:
        return RedirectResponse(f"{LOGIN_URL}?next={quote(request.url.path)}", status_code=303)

    @app.get(openapi_url, include_in_schema=False)
    def openapi_json(request: Request) -> JSONResponse:
        if session.current_admin(request) is None:
            raise HTTPException(status_code=401, detail="Iniciá sesión en el panel para ver la documentación.")
        return JSONResponse(app.openapi())

    @app.get("/docs", include_in_schema=False)
    def swagger_ui(request: Request):
        if session.current_admin(request) is None:
            return _login(request)
        return get_swagger_ui_html(openapi_url=openapi_url, title=f"{app.title} - Swagger UI", swagger_favicon_url=FAVICON_URL)

    @app.get("/redoc", include_in_schema=False)
    def redoc(request: Request):
        if session.current_admin(request) is None:
            return _login(request)
        return get_redoc_html(openapi_url=openapi_url, title=f"{app.title} - ReDoc", redoc_favicon_url=FAVICON_URL)
