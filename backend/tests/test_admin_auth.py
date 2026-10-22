"""
Panel /admin: roles (admin y solo lectura), zona de usuarios y login con Clerk.

Los tokens de Clerk se firman con una clave RSA de prueba y la API de Clerk se reemplaza
por dobles: ningún test hace llamadas de red.
"""
import time
from types import SimpleNamespace

import jwt
import pytest
from clerk_backend_api.security.types import TokenVerificationError, TokenVerificationErrorReason
from clerk_backend_api.security.verifytoken import verify_token
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.admin import clerk_auth, session
from app.admin.router import PREFIX
from app.admin.router import router as admin_router
from app.core.config import settings
from app.db import admin_member_repository as members
from app.db import api_key_repository as repo
from app.db.database import init_db

USER, PASSWORD = "admin", "clave-de-prueba"
ORIGIN = "http://testserver"

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _private_key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
)
PUBLIC_PEM = _private_key.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
).decode()
OTHER_PRIVATE_PEM = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "ADMIN_USERNAME", USER)
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", SecretStr(PASSWORD))
    monkeypatch.setattr(settings, "ADMIN_AUTH", "ambos")
    monkeypatch.setattr(settings, "ADMIN_CLERK_PUBLISHABLE_KEY", None)
    monkeypatch.setattr(settings, "ADMIN_CLERK_SECRET_KEY", None)
    monkeypatch.setattr(settings, "ADMIN_CLERK_JWT_KEY", None)
    init_db()
    session.reset()
    app = FastAPI()
    app.include_router(admin_router, prefix=PREFIX)
    return TestClient(app)


@pytest.fixture
def clerk_on(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_CLERK_PUBLISHABLE_KEY", "pk_test_ZGVtby5jbGVyay5hY2NvdW50cy5kZXYk")
    monkeypatch.setattr(settings, "ADMIN_CLERK_SECRET_KEY", SecretStr("sk_test_prueba"))
    monkeypatch.setattr(settings, "ADMIN_CLERK_JWT_KEY", PUBLIC_PEM)


def as_github(client, username: str) -> TestClient:
    client.cookies.set(session.COOKIE_NAME, session.create_token(session.github_subject(username)), path=PREFIX)
    return client


def clerk_token(sub="user_123", azp=ORIGIN, exp_in=3600, key=PRIVATE_PEM, algorithm="RS256", **extra) -> str:
    # 1 h de vigencia: el token no vence aunque la suite tarde (Issues #114 y #115)
    now = int(time.time())
    claims = {"sub": sub, "azp": azp, "iat": now, "nbf": now, "exp": now + exp_in, "sid": "sess_1", **extra}
    return jwt.encode(claims, key, algorithm=algorithm)


# ---------------------------------------------------------------------------
# Usuarios semilla y roles
# ---------------------------------------------------------------------------

def test_semilla_desde_el_env_no_pisa_cambios(client, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_GITHUB_ADMINS", "JAMRdev, @malessan4")
    monkeypatch.setattr(settings, "ADMIN_GITHUB_VIEWERS", "Jos022,JAMRdev,usuario inválido")
    assert settings.admin_github_seed == {"Jos022": "viewer", "JAMRdev": "admin", "usuario inválido": "viewer", "malessan4": "admin"}
    assert members.seed(settings.admin_github_seed) == 3

    jos = members.get_by_username("jos022")  # sin distinguir mayúsculas
    members.set_role(jos["id"], "admin")
    assert members.seed(settings.admin_github_seed) == 0
    assert members.get_by_username("Jos022")["role"] == "admin"


def test_solo_lectura_ve_todo_pero_no_modifica(client):
    members.add("Silvanam1", "viewer")
    key_id = repo.create(label="c", raw_key="sk-123456789", provider="groq", base_url=None, model="m",
                         priority=1, extra_params={}, rpm_threshold=None, tpm_threshold=None,
                         rpd_threshold=None, tpd_threshold=None)
    viewer = as_github(client, "Silvanam1")

    page = viewer.get(PREFIX + "/")
    assert page.status_code == 200
    assert "Modo solo lectura" in page.text
    assert "btn-new-connection" not in page.text and "tab-usuarios" not in page.text
    assert viewer.get(PREFIX + "/api/metrics").status_code == 200
    assert viewer.get(PREFIX + "/diagramas/arquitectura").status_code == 200

    assert viewer.post(PREFIX + "/keys", data={"label": "x", "key_value": "k", "provider": "groq", "model": "m"}).status_code == 403
    assert viewer.post(f"{PREFIX}/keys/{key_id}/enabled", data={"enabled": "false"}).status_code == 403
    assert viewer.post(f"{PREFIX}/keys/{key_id}/delete").status_code == 403
    assert viewer.get(PREFIX + "/api/members").status_code == 403
    assert viewer.post(PREFIX + "/api/members", json={"github_username": "otro", "role": "admin"}).status_code == 403
    assert len(repo.list_all_ordered_by_priority()) == 1


def test_admin_de_github_ve_la_zona_de_usuarios(client):
    members.add("JAMRdev", "admin")
    page = as_github(client, "JAMRdev").get(PREFIX + "/")
    assert "tab-usuarios" in page.text and "btn-new-connection" in page.text
    assert "Modo solo lectura" not in page.text


def test_quitar_o_suspender_a_alguien_corta_su_sesion_al_instante(client):
    member = members.add("Jos022", "viewer")
    viewer = as_github(client, "Jos022")
    assert viewer.get(PREFIX + "/api/metrics").status_code == 200
    members.set_enabled(member["id"], False)
    assert viewer.get(PREFIX + "/api/metrics").status_code == 401
    members.set_enabled(member["id"], True)
    members.delete(member["id"])
    assert viewer.get(PREFIX + "/api/metrics").status_code == 401


def test_usuario_de_github_desconocido_no_entra(client):
    assert as_github(client, "intruso").get(PREFIX + "/api/metrics").status_code == 401


# ---------------------------------------------------------------------------
# Zona de usuarios
# ---------------------------------------------------------------------------

def test_admin_agrega_cambia_rol_suspende_y_quita(client):
    members.add("JAMRdev", "admin")
    admin = as_github(client, "JAMRdev")

    r = admin.post(PREFIX + "/api/members", json={"github_username": "@VictoriaMackenzie", "role": "viewer"})
    assert r.status_code == 201
    vic = next(m for m in r.json()["members"] if m["github_username"] == "VictoriaMackenzie")
    assert vic["role"] == "viewer" and vic["added_by"] == "JAMRdev"
    assert r.json()["local_account"]["username"] == USER

    r = admin.patch(f"{PREFIX}/api/members/{vic['id']}", json={"role": "admin", "enabled": False})
    vic = next(m for m in r.json()["members"] if m["id"] == vic["id"])
    assert (vic["role"], vic["enabled"]) == ("admin", False)

    r = admin.delete(f"{PREFIX}/api/members/{vic['id']}")
    assert [m["github_username"] for m in r.json()["members"]] == ["JAMRdev"]


@pytest.mark.parametrize("username", ["", "-guion", "con espacio", "a" * 40, "doble--guion"])
def test_usuario_de_github_invalido(client, username):
    members.add("JAMRdev", "admin")
    r = as_github(client, "JAMRdev").post(PREFIX + "/api/members", json={"github_username": username, "role": "viewer"})
    assert r.status_code == 422


def test_no_se_duplica_ni_acepta_roles_inventados(client):
    members.add("JAMRdev", "admin")
    admin = as_github(client, "JAMRdev")
    assert admin.post(PREFIX + "/api/members", json={"github_username": "jamrdev", "role": "viewer"}).status_code == 422
    assert admin.post(PREFIX + "/api/members", json={"github_username": "otro", "role": "superadmin"}).status_code == 422


def test_nadie_cambia_su_propio_acceso(client):
    me = members.add("JAMRdev", "admin")
    admin = as_github(client, "JAMRdev")
    assert admin.patch(f"{PREFIX}/api/members/{me['id']}", json={"role": "viewer"}).status_code == 409
    assert admin.delete(f"{PREFIX}/api/members/{me['id']}").status_code == 409
    assert members.get(me["id"])["role"] == "admin"


def test_la_cuenta_del_env_administra_usuarios(client):
    client.post(PREFIX + "/login", data={"username": USER, "password": PASSWORD}, follow_redirects=False)
    assert client.post(PREFIX + "/api/members", json={"github_username": "camposmatias", "role": "viewer"}).status_code == 201


def test_cambios_de_usuarios_desde_otro_origen_se_rechazan(client):
    members.add("JAMRdev", "admin")
    r = as_github(client, "JAMRdev").post(
        PREFIX + "/api/members", json={"github_username": "otro", "role": "admin"},
        headers={"Origin": "https://malicioso.example"},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Clerk
# ---------------------------------------------------------------------------

def test_frontend_api_sale_de_la_publishable_key():
    assert clerk_auth.frontend_api("pk_test_ZGVtby5jbGVyay5hY2NvdW50cy5kZXYk") == "demo.clerk.accounts.dev"
    assert clerk_auth.frontend_api("otra-cosa") is None
    assert clerk_auth.frontend_api(None) is None


def test_login_muestra_clerk_solo_si_esta_configurado(client, clerk_on):
    r = client.get(PREFIX + "/login")
    assert "demo.clerk.accounts.dev/npm/@clerk/clerk-js@6/dist/clerk.browser.js" in r.text
    assert "Entrar sin Clerk" in r.text


def test_sin_claves_de_clerk_queda_el_login_propio(client):
    r = client.get(PREFIX + "/login")
    assert "clerk.browser.js" not in r.text and 'name="password"' in r.text
    assert client.post(PREFIX + "/auth/clerk", json={"token": "x"}).status_code == 404


def test_modo_solo_clerk_desactiva_el_respaldo(client, clerk_on, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_AUTH", "clerk")
    assert 'name="password"' not in client.get(PREFIX + "/login").text
    assert client.post(PREFIX + "/login", data={"username": USER, "password": PASSWORD}).status_code == 404


def _github_identity(username="JAMRdev"):
    return clerk_auth.ClerkIdentity("github", username, "Jose", "https://img/avatar.png", "user_123")


def test_login_con_clerk_de_un_miembro(client, clerk_on, monkeypatch):
    members.add("JAMRdev", "admin")
    monkeypatch.setattr(clerk_auth, "fetch_identity", lambda user_id: _github_identity())
    r = client.post(PREFIX + "/auth/clerk", json={"token": clerk_token(), "next": f"{PREFIX}#usuarios"})
    assert r.status_code == 200 and r.json() == {"redirect": f"{PREFIX}#usuarios"}
    assert session.COOKIE_NAME in r.headers["set-cookie"]
    member = members.get_by_username("JAMRdev")
    assert member["avatar_url"] == "https://img/avatar.png" and member["last_login_at"]
    assert client.get(PREFIX + "/api/members").status_code == 200


def test_login_con_clerk_de_la_cuenta_del_env(client, clerk_on, monkeypatch):
    monkeypatch.setattr(clerk_auth, "fetch_identity",
                        lambda user_id: clerk_auth.ClerkIdentity("local", USER, None, None, "user_9"))
    assert client.post(PREFIX + "/auth/clerk", json={"token": clerk_token()}).status_code == 200
    assert client.get(PREFIX + "/api/members").status_code == 200


def test_login_con_clerk_de_alguien_sin_acceso(client, clerk_on, monkeypatch):
    monkeypatch.setattr(clerk_auth, "fetch_identity", lambda user_id: _github_identity("intruso"))
    r = client.post(PREFIX + "/auth/clerk", json={"token": clerk_token()})
    assert r.status_code == 403 and "@intruso no tiene acceso" in r.json()["detail"]
    assert "set-cookie" not in r.headers


@pytest.fixture
def rejections(monkeypatch):
    """Motivos con los que verify_token rechazó los tokens del request."""
    reasons = []

    def spy(token, options):
        try:
            return verify_token(token, options)
        except TokenVerificationError as error:
            reasons.append(error.reason)
            raise

    monkeypatch.setattr(clerk_auth, "verify_token", spy)
    return reasons


# Los tokens se firman dentro del test y no al recolectar: si no, con la suite lenta vencerían y
# el 401 llegaría por vencimiento y no por el defecto que se prueba (Issue #115). El motivo del
# rechazo se comprueba para que cada caso falle solo por su defecto.
@pytest.mark.parametrize("make_token, reason", [
    pytest.param(lambda: clerk_token(exp_in=-120), TokenVerificationErrorReason.TOKEN_EXPIRED, id="vencido"),
    pytest.param(lambda: clerk_token(azp="https://otro-sitio.example"),
                 TokenVerificationErrorReason.TOKEN_INVALID_AUTHORIZED_PARTIES, id="otro-origen"),
    pytest.param(lambda: clerk_token(key=OTHER_PRIVATE_PEM),
                 TokenVerificationErrorReason.TOKEN_INVALID_SIGNATURE, id="firmado-con-otra-clave"),
    pytest.param(lambda: clerk_token(key="una-clave-hmac-distinta-de-al-menos-32-bytes", algorithm="HS256"),
                 TokenVerificationErrorReason.TOKEN_INVALID, id="hmac-en-lugar-de-rsa"),
    pytest.param(lambda: "mt_token_de_maquina", None, id="no-es-de-sesion"),  # se rechaza antes de verificarlo
    pytest.param(lambda: "basura", TokenVerificationErrorReason.TOKEN_INVALID, id="mal-formado"),
])
def test_tokens_de_clerk_invalidos(client, clerk_on, monkeypatch, rejections, make_token, reason):
    monkeypatch.setattr(clerk_auth, "fetch_identity", lambda user_id: pytest.fail("no debería consultar Clerk"))
    r = client.post(PREFIX + "/auth/clerk", json={"token": make_token()})
    assert r.status_code == 401
    assert rejections == ([reason] if reason else [])


def test_si_clerk_no_responde_se_informa(client, clerk_on, monkeypatch):
    def down(user_id):
        raise ConnectionError("sin red")
    monkeypatch.setattr(clerk_auth, "fetch_identity", down)
    assert client.post(PREFIX + "/auth/clerk", json={"token": clerk_token()}).status_code == 502


def _account(provider="oauth_github", username="JAMRdev", status="verified"):
    return SimpleNamespace(provider=provider, username=username, verification=SimpleNamespace(status=status),
                           first_name="José", image_url="https://img/gh.png", avatar_url=None)


def _user(**overrides):
    base = dict(id="user_1", external_id=None, username=None, first_name=None, last_name=None,
                image_url="https://img/u.png", external_accounts=[])
    base.update(overrides)
    return SimpleNamespace(**base)


def test_identify_github_verificado(client):
    identity = clerk_auth.identify(_user(external_accounts=[_account()]))
    assert (identity.kind, identity.username, identity.avatar_url) == ("github", "JAMRdev", "https://img/gh.png")


def test_identify_rechaza_github_sin_verificar_y_usuarios_sueltos(client):
    with pytest.raises(clerk_auth.ClerkLoginError):
        clerk_auth.identify(_user(external_accounts=[_account(status="unverified")]))
    with pytest.raises(clerk_auth.ClerkLoginError):
        clerk_auth.identify(_user(external_accounts=[_account(provider="oauth_google")]))
    # Alguien que se registró con el mismo nombre de usuario pero no es la cuenta creada por el backend
    with pytest.raises(clerk_auth.ClerkLoginError):
        clerk_auth.identify(_user(username=USER))


def test_identify_cuenta_del_env(client):
    identity = clerk_auth.identify(_user(username=USER, external_id=clerk_auth.ENV_ADMIN_EXTERNAL_ID))
    assert identity.kind == "local" and identity.username == USER


class _FakeUsers:
    def __init__(self, existing=None, verified=True):
        self.existing, self.verified, self.calls = existing, verified, []

    def list(self, request, timeout_ms):
        self.calls.append(("list", request["username"]))
        return [self.existing] if self.existing else []

    def create(self, **kwargs):
        self.calls.append(("create", kwargs["username"], kwargs["external_id"]))

    def verify_password(self, user_id, password, timeout_ms):
        return SimpleNamespace(verified=self.verified)

    def update(self, user_id, password, timeout_ms):
        self.calls.append(("update", user_id))


@pytest.mark.parametrize("existing, verified, expected, action", [
    (None, True, "se creó", "create"),
    (SimpleNamespace(id="u1", external_id=clerk_auth.ENV_ADMIN_EXTERNAL_ID), True, "está al día", None),
    (SimpleNamespace(id="u1", external_id=clerk_auth.ENV_ADMIN_EXTERNAL_ID), False, "se actualizó", "update"),
    (SimpleNamespace(id="u1", external_id=None), True, "no fue creado por este backend", None),
])
def test_sincroniza_la_cuenta_del_env_en_clerk(client, clerk_on, monkeypatch, existing, verified, expected, action):
    users = _FakeUsers(existing, verified)
    monkeypatch.setattr(clerk_auth, "_client", lambda: SimpleNamespace(users=users))
    assert expected in clerk_auth.sync_env_admin()
    assert [c[0] for c in users.calls if c[0] != "list"] == ([action] if action else [])


def test_sincronizacion_no_rompe_si_clerk_falla(client, clerk_on, monkeypatch):
    class Broken:
        def list(self, **kwargs):
            raise ConnectionError("sin red")
    monkeypatch.setattr(clerk_auth, "_client", lambda: SimpleNamespace(users=Broken()))
    assert "no se pudo sincronizar" in clerk_auth.sync_env_admin()


# ---------------------------------------------------------------------------
# Deploy en Railway: clave PEM y tope global de intentos
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    PUBLIC_PEM,                                   # multilínea real (.env con comillas)
    PUBLIC_PEM.replace("\n", "\\n"),              # una línea con \n literales (panel de Railway)
    '"' + PUBLIC_PEM.replace("\n", "\\n") + '"',  # pegada con comillas incluidas
])
def test_la_clave_pem_se_normaliza(raw):
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    from app.core.config import Settings

    key = Settings.normalize_pem(raw)
    load_pem_public_key(key.encode())
    assert key.startswith("-----BEGIN PUBLIC KEY-----") and "\\n" not in key


def test_pem_vacia_queda_sin_configurar():
    from app.core.config import Settings

    assert Settings.normalize_pem("  ") is None and Settings.normalize_pem(None) is None


def test_un_token_de_clerk_con_pem_de_railway_valida(client, clerk_on, monkeypatch):
    from app.core.config import Settings

    monkeypatch.setattr(settings, "ADMIN_CLERK_JWT_KEY", Settings.normalize_pem(PUBLIC_PEM.replace("\n", "\\n")))
    members.add("JAMRdev", "admin")
    monkeypatch.setattr(clerk_auth, "fetch_identity", lambda user_id: _github_identity())
    assert client.post(PREFIX + "/auth/clerk", json={"token": clerk_token()}).status_code == 200


def test_tope_global_de_intentos_aunque_se_falsee_la_ip(client):
    def from_ip(ip):
        return SimpleNamespace(client=SimpleNamespace(host=ip))

    # Cada intento "desde" otra IP (como con X-Forwarded-For falseado): el límite por IP nunca se alcanza
    for i in range(session.MAX_FAILED_LOGINS_GLOBAL - 1):
        session.register_failed_login(from_ip(f"10.0.0.{i}"))
    assert session.login_blocked_for(from_ip("10.9.9.9")) == 0
    session.register_failed_login(from_ip("10.0.1.1"))
    assert session.login_blocked_for(from_ip("10.9.9.9")) > 0
    # También frena el login correcto mientras dura el bloqueo
    r = client.post(PREFIX + "/login", data={"username": USER, "password": PASSWORD})
    assert r.status_code == 429


def test_un_login_correcto_no_borra_el_contador_global(client):
    other = SimpleNamespace(client=SimpleNamespace(host="10.1.1.1"))
    for _ in range(3):
        session.register_failed_login(other)
    client.post(PREFIX + "/login", data={"username": USER, "password": PASSWORD})
    assert len(session._failed_logins[session._GLOBAL_KEY]) == 3
