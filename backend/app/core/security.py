from urllib.parse import urlparse

from fastapi import HTTPException, Request, status


def verify_same_origin(request: Request) -> None:
    """
    Mitigación CSRF para los POST del panel /admin (además de la cookie SameSite=Strict):
    se rechaza si el Origin/Referer no coincide con el host del backend.
    """
    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin:
        origin_host = urlparse(origin).netloc
        if origin_host and origin_host != request.url.netloc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Origen no permitido.",
            )
