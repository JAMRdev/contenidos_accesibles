"""
Capa de datos sobre Cloudflare D1 (Issue #59): adaptador D1Connection, verificación del
esquema al arrancar y paridad entre las migraciones de D1 y el esquema SQLite local.
"""
import sqlite3

import pytest

from app.core.config import settings
from app.db import api_key_repository as repo
from app.db import database
from app.db.d1_client import D1QueryError
from app.db.d1_connection import D1Connection, D1Row, to_positional
from app.db.database import SCHEMA, get_connection, init_db
from tests.fake_d1 import FakeD1Client, apply_migrations


@pytest.fixture
def d1(tmp_path, monkeypatch):
    """Backend en modo D1 con un D1 de prueba sobre SQLite (migraciones aplicadas)."""
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "d1.db"))
    monkeypatch.setattr(settings, "DATABASE_BACKEND", "d1")
    fake = FakeD1Client()
    monkeypatch.setattr(database, "_d1_client", fake)
    return fake


# ---------------------------------------------------------------------------
# Adaptador
# ---------------------------------------------------------------------------

def test_parametros_con_nombre_pasan_a_posicionales():
    sql, params = to_positional("INSERT INTO t VALUES (:a, :b) -- :a otra vez: :a", {"a": 1, "b": "x"})
    assert sql == "INSERT INTO t VALUES (?, ?) -- ? otra vez: ?"
    assert params == [1, "x", 1, 1]
    assert to_positional("SELECT ?", (5,)) == ("SELECT ?", [5])
    # Sin dict no se toca el texto (por ejemplo, JSON literal con ':')
    assert to_positional("""UPDATE t SET j = '{"a": 1}'""", None)[0] == """UPDATE t SET j = '{"a": 1}'"""


def test_fila_por_nombre_y_por_posicion():
    row = D1Row({"n": 3, "texto": "hola"})
    assert (row["n"], row[0], row[1], dict(row)) == (3, 3, "hola", {"n": 3, "texto": "hola"})


def test_las_escrituras_van_en_un_solo_batch_al_salir(d1):
    with get_connection() as conn:
        conn.execute("INSERT INTO admin_members (github_username, role) VALUES (?, ?)", ("a", "admin"))
        conn.execute("INSERT INTO admin_members (github_username, role) VALUES (?, ?)", ("b", "viewer"))
        assert d1.batches == 0  # todavía no salió nada
    assert d1.batches == 1
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM admin_members").fetchone()["n"] == 2


def test_un_error_en_el_bloque_descarta_las_escrituras(d1):
    with pytest.raises(RuntimeError), get_connection() as conn:
        conn.execute("INSERT INTO admin_members (github_username, role) VALUES (?, ?)", ("a", "admin"))
        raise RuntimeError("falla en el medio")
    assert d1.batches == 0
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM admin_members").fetchone()[0] == 0


def test_el_batch_es_atomico_si_falla_una_sentencia(d1):
    with pytest.raises(D1QueryError), get_connection() as conn:
        conn.execute("INSERT INTO admin_members (github_username, role) VALUES (?, ?)", ("a", "admin"))
        conn.execute("INSERT INTO admin_members (github_username, role) VALUES (?, ?)", ("b", "rol-invalido"))
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM admin_members").fetchone()[0] == 0


def test_rowcount_y_lastrowid_mandan_lo_pendiente(d1):
    with get_connection() as conn:
        cur = conn.execute("INSERT INTO admin_members (github_username, role) VALUES (?, ?)", ("a", "admin"))
        assert cur.lastrowid == 1 and cur.rowcount == 1
        second = conn.execute("UPDATE admin_members SET role = 'viewer' WHERE id = ?", (1,))
        third = conn.execute("UPDATE admin_members SET role = 'admin' WHERE id = ?", (99,))
        assert (second.rowcount, third.rowcount) == (1, 0)  # cada cursor lee su propio resultado
    assert d1.batches == 2


def test_una_lectura_ve_lo_escrito_antes_en_el_mismo_bloque(d1):
    with get_connection() as conn:
        conn.execute("INSERT INTO admin_members (github_username, role) VALUES (?, ?)", ("a", "admin"))
        assert conn.execute("SELECT github_username FROM admin_members").fetchall() == [{"github_username": "a"}]


def test_crear_conexion_de_ia_marca_la_primera_como_actual(d1):
    first = repo.create(label="A", raw_key="sk-aaaaaaaa", provider="groq", base_url=None, model="m", priority=1,
                        extra_params={}, rpm_threshold=None, tpm_threshold=None, rpd_threshold=None, tpd_threshold=None)
    repo.create(label="B", raw_key="sk-bbbbbbbb", provider="groq", base_url=None, model="m", priority=2,
                extra_params={}, rpm_threshold=None, tpm_threshold=None, rpd_threshold=None, tpd_threshold=None)
    rows = repo.list_all_ordered_by_priority()
    assert [(r["id"], r["is_current"]) for r in rows] == [(first, 1), (first + 1, 0)]


def test_cambiar_la_conexion_actual_es_atomico(d1):
    ids = [repo.create(label=n, raw_key=f"sk-{n}xxxxxxx", provider="groq", base_url=None, model="m", priority=i,
                       extra_params={}, rpm_threshold=None, tpm_threshold=None, rpd_threshold=None, tpd_threshold=None)
           for i, n in enumerate("ab")]
    before = d1.batches
    repo.set_current(ids[1])
    assert d1.batches == before + 1  # las dos actualizaciones en un solo batch
    assert [r["is_current"] for r in repo.list_all_ordered_by_priority()] == [0, 1]


def test_d1_no_admite_executescript():
    with pytest.raises(NotImplementedError):
        D1Connection(FakeD1Client()).executescript("SELECT 1")


# ---------------------------------------------------------------------------
# Arranque y esquema
# ---------------------------------------------------------------------------

def test_init_db_en_d1_verifica_las_tablas(d1):
    init_db()  # migraciones aplicadas por el D1 de prueba: arranca


def test_init_db_en_d1_sin_migraciones_explica_que_hacer(d1, monkeypatch):
    monkeypatch.setattr("tests.fake_d1.apply_migrations", lambda path: None)
    monkeypatch.setattr("tests.fake_d1._migrated", set())
    with pytest.raises(RuntimeError, match="npm run migrate:remote"):
        init_db()


def _schema(conn: sqlite3.Connection) -> dict:
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND name != 'gateway_healthcheck'")]
    out = {}
    for table in tables:
        out[table] = [(c[1], c[2].upper(), c[3], c[4], c[5]) for c in conn.execute(f"PRAGMA table_info({table})")]
    indexes = sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"))
    return {"tables": out, "indexes": indexes}


def test_las_migraciones_de_d1_coinciden_con_el_esquema_sqlite(tmp_path):
    local = sqlite3.connect(tmp_path / "local.db")
    local.executescript(SCHEMA)
    migrated_path = str(tmp_path / "d1.db")
    apply_migrations(migrated_path)
    migrated = sqlite3.connect(migrated_path)
    assert _schema(migrated) == _schema(local)


# ---------------------------------------------------------------------------
# Migración de datos SQLite → D1
# ---------------------------------------------------------------------------

def _source_db(tmp_path) -> str:
    """SQLite como el del volumen de Railway, con datos en varias tablas."""
    path = str(tmp_path / "origen.db")
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO api_keys (id, label, encrypted_key, provider, model, priority, is_current) "
                 "VALUES (5, 'Groq', 'gAAAA-cifrada', 'groq', 'm', 0, 1)")
    conn.execute("INSERT INTO admin_members (github_username, role) VALUES ('JAMRdev', 'admin')")
    conn.execute("INSERT INTO ai_usage_events (created_at, connection_label, provider, model, kind, outcome, latency_ms) "
                 "VALUES ('2026-10-15T00:00:00', 'Groq', 'groq', 'm', 'text', 'ok', 120)")
    conn.commit()
    conn.close()
    return path


def test_migra_todas_las_tablas_conservando_ids_y_keys_cifradas(d1, tmp_path):
    from scripts.migrate_sqlite_to_d1 import migrate

    copied = migrate(_source_db(tmp_path), d1, out=lambda *_: None)
    assert copied["api_keys"] == 1 and copied["admin_members"] == 1 and copied["ai_usage_events"] == 1
    row = d1.query("SELECT id, encrypted_key, is_current FROM api_keys").rows[0]
    assert row == {"id": 5, "encrypted_key": "gAAAA-cifrada", "is_current": 1}


def test_dry_run_no_escribe(d1, tmp_path):
    from scripts.migrate_sqlite_to_d1 import migrate

    assert migrate(_source_db(tmp_path), d1, dry_run=True, out=lambda *_: None)["api_keys"] == 1
    assert d1.query("SELECT COUNT(*) AS n FROM api_keys").rows[0]["n"] == 0


def test_no_pisa_una_tabla_con_datos_salvo_con_force(d1, tmp_path):
    from scripts.migrate_sqlite_to_d1 import migrate

    source = _source_db(tmp_path)
    migrate(source, d1, out=lambda *_: None)
    with pytest.raises(SystemExit, match="ya tiene 1 filas"):
        migrate(source, d1, out=lambda *_: None)
    migrate(source, d1, force=True, out=lambda *_: None)
    assert d1.query("SELECT COUNT(*) AS n FROM api_keys").rows[0]["n"] == 1


def test_origen_viejo_sin_tablas_nuevas(d1, tmp_path):
    from scripts.migrate_sqlite_to_d1 import migrate

    path = str(tmp_path / "viejo.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE api_keys (id INTEGER PRIMARY KEY, label TEXT NOT NULL, encrypted_key TEXT NOT NULL)")
    conn.execute("INSERT INTO api_keys VALUES (1, 'vieja', 'cifrada')")
    conn.commit()
    conn.close()
    copied = migrate(path, d1, out=lambda *_: None)
    assert copied == {"api_keys": 1}
    assert d1.query("SELECT provider, priority FROM api_keys").rows == [{"provider": "gemini", "priority": 100}]


def test_init_db_en_d1_sin_la_columna_user_id_explica_que_hacer(d1):
    init_db()
    with database.get_connection() as conn:
        conn.execute("ALTER TABLE adaptation_decisions DROP COLUMN user_id")
    with pytest.raises(RuntimeError, match="adaptation_decisions.*user_id.*npm run migrate:remote"):
        init_db()
