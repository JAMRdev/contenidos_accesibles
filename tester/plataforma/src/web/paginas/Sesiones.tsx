// Sesiones de QA: listado con filtros y alta de una sesión nueva.
import { useState, type FormEvent } from "react";
import { useApi, useDatos, useYo } from "../api";
import { Link, useFiltros, useRuta } from "../ruta";
import type { Area, Avance, Entorno, Sesion } from "../tipos";
import { Alerta, AREA_TEXTO, AREAS, Busqueda, Cargando, ENTORNO_TEXTO, fecha, Icon, RESULTADO_INFO, Selector } from "../ui";

type Lista = { sesiones: (Sesion & { avance: Avance })[]; siguiente: number | null };

// URL sugerida según el área y el entorno (se puede cambiar)
const URL_SUGERIDA: Record<Area, Record<Entorno, string>> = {
    backend: { produccion: "https://innovalab-educacion-e18-test.up.railway.app/api/v1", staging: "", local: "http://127.0.0.1:8000/api/v1" },
    frontend: { produccion: "https://contenidos-accesibles.vercel.app", staging: "", local: "http://localhost:5173" },
};

export function BarraAvance({ avance }: { avance: Avance }) {
    const pct = (n: number) => `${(n / Math.max(avance.total, 1)) * 100}%`;
    const texto = (["paso", "fallo", "bloqueado", "omitido", "pendiente"] as const).map((e) => `${RESULTADO_INFO[e].label}: ${avance[e]}`).join(", ");
    return (
        <div className="qa-progress" role="img" aria-label={`Avance: ${texto}`}>
            {(["paso", "fallo", "bloqueado", "omitido"] as const).map((e) => (
                <span key={e} className={e} style={{ width: pct(avance[e]) }} />
            ))}
        </div>
    );
}

function NuevaSesion({ alCancelar }: { alCancelar: () => void }) {
    const api = useApi();
    const { ir } = useRuta();
    const [datos, setDatos] = useState({
        titulo: `Ronda ${new Date().toLocaleDateString("es-AR")}`,
        area: "backend" as Area,
        entorno: "produccion" as Entorno,
        url: URL_SUGERIDA.backend.produccion,
        version: "",
        notas: "",
    });
    const sugerir = (area: Area, entorno: Entorno) => setDatos({ ...datos, area, entorno, url: URL_SUGERIDA[area][entorno] || datos.url });
    const [error, setError] = useState<string | null>(null);
    const [detectando, setDetectando] = useState(false);

    // Versión del backend probado: GET <origen>/api/v1/health (si el backend permite CORS desde acá)
    async function detectarVersion() {
        setDetectando(true);
        try {
            const base = datos.url.replace(/\/+$/, "");
            const res = await fetch(`${base.endsWith("/api/v1") ? base : `${base}/api/v1`}/health`);
            const health = (await res.json()) as { version?: string };
            if (health.version) setDatos((d) => ({ ...d, version: health.version! }));
            else setError("El backend respondió sin versión: escribila a mano.");
        } catch {
            setError("No se pudo leer /health del backend (puede estar dormido o no permitir CORS): escribí la versión a mano.");
        } finally {
            setDetectando(false);
        }
    }

    async function crear(ev: FormEvent) {
        ev.preventDefault();
        try {
            const s = await api<Sesion>("/api/sesiones", { method: "POST", body: JSON.stringify({ ...datos, version: datos.version || null, notas: datos.notas || null }) });
            ir(`/sesiones/${s.id}`);
        } catch (e) {
            setError((e as Error).message);
        }
    }

    return (
        <form className="qa-card mb-4" onSubmit={crear}>
            <fieldset className="form-section">
                <legend className="form-section-title">
                    <Icon name="add_circle" /> Nueva sesión
                </legend>
                <div className="form-grid form-grid-3">
                    <div className="form-group span-3">
                        <div className="form-label-container">
                            <label className="form-label" htmlFor="ses-titulo">Título</label>
                        </div>
                        <input className="form-control" id="ses-titulo" value={datos.titulo} onChange={(e) => setDatos({ ...datos, titulo: e.target.value })} required />
                    </div>
                    <Selector id="ses-area" etiqueta="Área" valor={datos.area} opciones={AREAS.map((a) => [a, AREA_TEXTO[a]] as const)} onChange={(area) => sugerir(area, datos.entorno)} />
                    <Selector
                        id="ses-entorno"
                        etiqueta="Entorno"
                        valor={datos.entorno}
                        opciones={(Object.keys(ENTORNO_TEXTO) as Entorno[]).map((e) => [e, ENTORNO_TEXTO[e]] as const)}
                        onChange={(entorno) => sugerir(datos.area, entorno)}
                    />
                    <div className="form-group">
                        <div className="form-label-container">
                            <label className="form-label" htmlFor="ses-version">Versión probada</label>
                        </div>
                        <div className="qa-input-action">
                            <input className="form-control" id="ses-version" value={datos.version} placeholder="0.1.0" onChange={(e) => setDatos({ ...datos, version: e.target.value })} />
                            {datos.area === "backend" && (
                                <button type="button" className="btn btn-sm btn-outline-secondary" onClick={() => void detectarVersion()} disabled={detectando}>
                                    <Icon name="manage_search" /> Detectar
                                </button>
                            )}
                        </div>
                    </div>
                    <div className="form-group span-3">
                        <div className="form-label-container">
                            <label className="form-label" htmlFor="ses-url">{datos.area === "backend" ? "URL de la API probada" : "URL del frontend probado"}</label>
                        </div>
                        <input className="form-control" id="ses-url" type="url" value={datos.url} onChange={(e) => setDatos({ ...datos, url: e.target.value })} required />
                    </div>
                    <div className="form-group span-3">
                        <div className="form-label-container">
                            <label className="form-label" htmlFor="ses-notas">Notas (opcional)</label>
                        </div>
                        <textarea className="form-control" id="ses-notas" value={datos.notas} onChange={(e) => setDatos({ ...datos, notas: e.target.value })} />
                    </div>
                </div>
                {datos.entorno === "produccion" && (
                    <p className="admin-note">
                        <Icon name="info" /> En producción no se listan los casos destructivos (estrés, cambios de configuración): se prueban en local o staging.
                    </p>
                )}
            </fieldset>
            {error && <Alerta tipo="danger">{error}</Alerta>}
            <div className="qa-actions">
                <button type="submit" className="btn btn-primary">
                    <Icon name="play_arrow" /> Crear y empezar
                </button>
                <button type="button" className="btn btn-outline-secondary" onClick={alCancelar}>
                    Cancelar
                </button>
            </div>
        </form>
    );
}

export function Sesiones() {
    const yo = useYo();
    const [f, setF] = useFiltros({ q: "", estado: "", area: "", entorno: "", nueva: "" });
    const consulta = new URLSearchParams(Object.entries({ q: f.q, estado: f.estado, area: f.area, entorno: f.entorno }).filter(([, v]) => v)).toString();
    const lista = useDatos<Lista>(`/api/sesiones?limite=50&${consulta}`);

    return (
        <>
            <div className="admin-section-head">
                <div>
                    <h2 className="admin-h2">Sesiones</h2>
                    <p className="admin-muted admin-section-lead">Cada sesión es una ronda de pruebas de un área (backend o frontend) contra un entorno y una versión.</p>
                </div>
                {yo.permisos.escribir && !f.nueva && (
                    <button type="button" className="btn btn-primary" onClick={() => setF({ nueva: "1" })}>
                        <Icon name="add" /> Nueva sesión
                    </button>
                )}
            </div>

            {f.nueva && yo.permisos.escribir && <NuevaSesion alCancelar={() => setF({ nueva: "" })} />}

            <div className="table-filters qa-filters">
                <Busqueda id="ses-q" etiqueta="Buscar" valor={f.q} onChange={(q) => setF({ q })} placeholder="Título o notas" />
                <Selector id="ses-estado" etiqueta="Estado" valor={f.estado} opciones={[["", "Todas"], ["abierta", "Abiertas"], ["cerrada", "Cerradas"]]} onChange={(estado) => setF({ estado })} />
                <Selector id="ses-f-area" etiqueta="Área" valor={f.area} opciones={[["", "Todas"], ...AREAS.map((a) => [a, AREA_TEXTO[a]] as const)]} onChange={(area) => setF({ area })} />
                <Selector
                    id="ses-f-entorno"
                    etiqueta="Entorno"
                    valor={f.entorno}
                    opciones={[["", "Todos"], ...(Object.keys(ENTORNO_TEXTO) as Entorno[]).map((e) => [e, ENTORNO_TEXTO[e]] as const)]}
                    onChange={(entorno) => setF({ entorno })}
                />
            </div>

            {lista.error && <Alerta tipo="danger">{lista.error}</Alerta>}
            {lista.cargando && !lista.datos && <Cargando />}
            {lista.datos && (
                <div className="table-card">
                    <table className="table table-borderless admin-table">
                        <caption className="visually-hidden">Sesiones de QA</caption>
                        <thead>
                            <tr>
                                <th scope="col">Sesión</th>
                                <th scope="col">Área y entorno</th>
                                <th scope="col">Avance</th>
                                <th scope="col">Falló</th>
                                <th scope="col">Estado</th>
                                <th scope="col">Abierta por</th>
                            </tr>
                        </thead>
                        <tbody>
                            {!lista.datos.sesiones.length && (
                                <tr>
                                    <td colSpan={6} className="admin-muted">No hay sesiones con estos filtros.</td>
                                </tr>
                            )}
                            {lista.datos.sesiones.map((s) => (
                                <tr key={s.id}>
                                    <td>
                                        <Link className="cell-title" href={`/sesiones/${s.id}`}>{s.titulo}</Link>
                                        <span className="cell-sub">{s.version ? `Versión ${s.version}` : "Sin versión"} · #{s.id}</span>
                                    </td>
                                    <td>{AREA_TEXTO[s.area]}<span className="cell-sub">{ENTORNO_TEXTO[s.entorno]}</span></td>
                                    <td>
                                        <BarraAvance avance={s.avance} />
                                        <span className="cell-sub">{s.avance.total - s.avance.pendiente} de {s.avance.total} casos</span>
                                    </td>
                                    <td>{s.avance.fallo}</td>
                                    <td>
                                        <span className={`status status-${s.estado === "abierta" ? "active" : "disabled"}`}>
                                            <Icon name={s.estado === "abierta" ? "play_circle" : "lock"} />
                                            {s.estado === "abierta" ? "Abierta" : "Cerrada"}
                                        </span>
                                    </td>
                                    <td>
                                        @{s.creada_por}
                                        <span className="cell-sub">{fecha(s.creada_en)}</span>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </>
    );
}
