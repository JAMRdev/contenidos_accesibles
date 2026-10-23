// Catálogo de casos por área: los del repo (backend, solo lectura) y los manuales (se crean, editan y
// archivan acá; sirven, por ejemplo, para el frontend, que no tiene casos en el repo).
import { useState, type FormEvent } from "react";
import { useApi, useDatos, useYo } from "../api";
import { Link, useFiltros, useRuta } from "../ruta";
import type { Area, Caso, Entorno, EstadoBug, EstadoResultado, Seccion, Severidad } from "../tipos";
import { Alerta, AREA_TEXTO, AREAS, BUG_INFO, Busqueda, Cargando, ENTORNO_TEXTO, Estado, fecha, Icon, Marcas, RESULTADO_INFO, Selector, SEVERIDAD_TEXTO, useToast } from "../ui";

const SEVERIDADES = Object.keys(SEVERIDAD_TEXTO) as Severidad[];

type Formulario = {
    area: Area;
    seccion: string;
    titulo: string;
    ubicacion: string;
    precondiciones: string;
    pasos: string;
    datos: string;
    esperado: string;
    severidad: Severidad;
    etiquetas: string;
    mock: boolean;
    ia_real: boolean;
    destructivo: boolean;
};

const lineas = (t: string) => t.split("\n").map((l) => l.trim()).filter(Boolean);

function formularioDe(caso: Caso | null, area: Area): Formulario {
    return {
        area: caso?.area ?? area,
        seccion: caso?.seccion ?? "",
        titulo: caso?.titulo ?? "",
        ubicacion: caso?.endpoint ?? "",
        precondiciones: caso?.precondiciones.join("\n") ?? "",
        pasos: caso?.pasos.join("\n") ?? "",
        datos: caso?.datos ?? "",
        esperado: caso?.esperado ?? "",
        severidad: caso?.severidad ?? "media",
        etiquetas: caso?.etiquetas.join(", ") ?? "",
        mock: caso?.mock ?? false,
        ia_real: caso?.ia_real ?? false,
        destructivo: caso?.destructivo ?? false,
    };
}

/** Alta o edición de un caso manual. */
function CasoFormulario({ caso, area, alGuardar, alCancelar }: { caso: Caso | null; area: Area; alGuardar: (c: Caso) => void; alCancelar: () => void }) {
    const api = useApi();
    const toast = useToast();
    const [f, setF] = useState<Formulario>(() => formularioDe(caso, area));
    const [error, setError] = useState<string | null>(null);
    const [guardando, setGuardando] = useState(false);
    const secciones = useDatos<{ secciones: Seccion[] }>(`/api/casos/secciones?area=${f.area}`);
    const set = (campo: keyof Formulario) => (e: { target: { value: string } }) => setF({ ...f, [campo]: e.target.value });
    const marca = (campo: "mock" | "ia_real" | "destructivo") => (e: { target: { checked: boolean } }) => setF({ ...f, [campo]: e.target.checked });

    async function guardar(ev: FormEvent) {
        ev.preventDefault();
        setGuardando(true);
        const datos = {
            seccion: f.seccion,
            titulo: f.titulo,
            ubicacion: f.ubicacion || null,
            precondiciones: lineas(f.precondiciones),
            pasos: lineas(f.pasos),
            datos: f.datos || null,
            esperado: f.esperado,
            severidad: f.severidad,
            etiquetas: f.etiquetas.split(",").map((e) => e.trim().toLowerCase().replace(/\s+/g, "-")).filter(Boolean),
            mock: f.mock,
            ia_real: f.ia_real,
            destructivo: f.destructivo,
        };
        try {
            const guardado = caso
                ? await api<Caso>(`/api/casos/${caso.id}`, { method: "PATCH", body: JSON.stringify(datos) })
                : await api<Caso>("/api/casos", { method: "POST", body: JSON.stringify({ ...datos, area: f.area }) });
            toast(caso ? `Caso ${guardado.id} actualizado.` : `Caso ${guardado.id} creado.`);
            alGuardar(guardado);
        } catch (e) {
            setError((e as Error).message);
        } finally {
            setGuardando(false);
        }
    }

    return (
        <form className="qa-card qa-stack" onSubmit={guardar} noValidate>
            <h2 className="qa-card-title mb-0">{caso ? `Editar ${caso.id}` : "Nuevo caso"}</h2>
            <div className="form-grid form-grid-3">
                {!caso && <Selector id="cf-area" etiqueta="Área" valor={f.area} opciones={AREAS.map((a) => [a, AREA_TEXTO[a]] as const)} onChange={(area) => setF({ ...f, area })} />}
                <div className={`form-group${caso ? " span-2" : ""}`}>
                    <div className="form-label-container">
                        <label className="form-label" htmlFor="cf-seccion">Caso de uso o sección</label>
                    </div>
                    <input className="form-control" id="cf-seccion" list="cf-secciones" value={f.seccion} onChange={set("seccion")} placeholder="Por ejemplo: Login de docentes" />
                    <datalist id="cf-secciones">
                        {(secciones.datos?.secciones ?? []).map((s) => <option key={s.seccion} value={s.seccion} />)}
                    </datalist>
                </div>
                <Selector id="cf-severidad" etiqueta="Severidad si falla" valor={f.severidad} opciones={SEVERIDADES.map((s) => [s, SEVERIDAD_TEXTO[s]] as const)} onChange={(severidad) => setF({ ...f, severidad })} />
                <div className="form-group span-2">
                    <div className="form-label-container"><label className="form-label" htmlFor="cf-titulo">Título</label></div>
                    <input className="form-control" id="cf-titulo" value={f.titulo} onChange={set("titulo")} placeholder="Qué se prueba, en una frase" />
                </div>
                <div className="form-group">
                    <div className="form-label-container"><label className="form-label" htmlFor="cf-ubicacion">Pantalla o endpoint (opcional)</label></div>
                    <input className="form-control" id="cf-ubicacion" value={f.ubicacion} onChange={set("ubicacion")} placeholder="/login o POST /api/v1/…" />
                </div>
                <div className="form-group span-3">
                    <div className="form-label-container">
                        <label className="form-label" htmlFor="cf-pasos">Pasos</label>
                        <p className="form-label-description admin-muted small mb-0">Uno por línea.</p>
                    </div>
                    <textarea className="form-control" id="cf-pasos" rows={4} value={f.pasos} onChange={set("pasos")} />
                </div>
                <div className="form-group span-3">
                    <div className="form-label-container"><label className="form-label" htmlFor="cf-esperado">Resultado esperado</label></div>
                    <textarea className="form-control" id="cf-esperado" rows={3} value={f.esperado} onChange={set("esperado")} />
                </div>
                <div className="form-group span-2">
                    <div className="form-label-container">
                        <label className="form-label" htmlFor="cf-precondiciones">Precondiciones (opcional)</label>
                        <p className="form-label-description admin-muted small mb-0">Una por línea.</p>
                    </div>
                    <textarea className="form-control" id="cf-precondiciones" rows={3} value={f.precondiciones} onChange={set("precondiciones")} />
                </div>
                <div className="form-group">
                    <div className="form-label-container"><label className="form-label" htmlFor="cf-datos">Datos de prueba (opcional)</label></div>
                    <textarea className="form-control" id="cf-datos" rows={3} value={f.datos} onChange={set("datos")} />
                </div>
                <div className="form-group span-2">
                    <div className="form-label-container">
                        <label className="form-label" htmlFor="cf-etiquetas">Etiquetas (opcional)</label>
                        <p className="form-label-description admin-muted small mb-0">Separadas por comas.</p>
                    </div>
                    <input className="form-control" id="cf-etiquetas" value={f.etiquetas} onChange={set("etiquetas")} placeholder="login, accesibilidad" />
                </div>
                <fieldset className="form-group qa-checks">
                    <legend className="form-label">Marcas</legend>
                    <div className="form-check"><input className="form-check-input" type="checkbox" id="cf-mock" checked={f.mock} onChange={marca("mock")} /><label className="form-check-label" htmlFor="cf-mock">Se prueba con mock</label></div>
                    <div className="form-check"><input className="form-check-input" type="checkbox" id="cf-ia" checked={f.ia_real} onChange={marca("ia_real")} /><label className="form-check-label" htmlFor="cf-ia">Consume IA real</label></div>
                    <div className="form-check"><input className="form-check-input" type="checkbox" id="cf-destructivo" checked={f.destructivo} onChange={marca("destructivo")} /><label className="form-check-label" htmlFor="cf-destructivo">Solo en local o staging</label></div>
                </fieldset>
            </div>
            {error && <Alerta tipo="danger">{error}</Alerta>}
            <div className="qa-actions">
                <button type="submit" className="btn btn-primary" disabled={guardando}><Icon name="save" /> {caso ? "Guardar cambios" : "Crear caso"}</button>
                <button type="button" className="btn btn-outline-secondary" onClick={alCancelar}>Cancelar</button>
            </div>
        </form>
    );
}

export function Casos() {
    const yo = useYo();
    const { ir } = useRuta();
    const [f, setF] = useFiltros({ area: "backend", q: "", seccion: "", marcas: "", archivados: "", nuevo: "" });
    const area = (AREAS as string[]).includes(f.area) ? (f.area as Area) : "backend";
    const consulta = new URLSearchParams(
        Object.entries({ area, q: f.q, seccion: f.seccion, archivados: f.archivados, ...(f.marcas ? { [f.marcas]: "true" } : {}) }).filter(([, v]) => v),
    ).toString();
    const lista = useDatos<{ total: number; casos: Caso[] }>(`/api/casos?${consulta}`);
    const secciones = useDatos<{ secciones: Seccion[] }>(`/api/casos/secciones?area=${area}`);

    return (
        <>
            <div className="admin-section-head">
                <div>
                    <h1 className="admin-h2">Catálogo de casos</h1>
                    <p className="admin-muted admin-section-lead">
                        {area === "backend"
                            ? "Los casos del backend vienen del repo (tester/casos) y se cambian con un PR. También podés sumar casos manuales."
                            : "Los casos del frontend se crean acá, agrupados por caso de uso. No se borran: se archivan."}
                    </p>
                </div>
                {yo.permisos.escribir && !f.nuevo && (
                    <button type="button" className="btn btn-primary" onClick={() => setF({ nuevo: "1" })}>
                        <Icon name="add" /> Nuevo caso
                    </button>
                )}
            </div>

            <nav className="qa-tabs" aria-label="Área">
                {AREAS.map((a) => (
                    <button key={a} type="button" className={`qa-tab${a === area ? " is-active" : ""}`} aria-pressed={a === area} onClick={() => setF({ area: a, seccion: "" })}>
                        <Icon name={a === "backend" ? "dns" : "web"} /> {AREA_TEXTO[a]}
                    </button>
                ))}
            </nav>

            {f.nuevo && yo.permisos.escribir && (
                <div className="mb-4">
                    <CasoFormulario caso={null} area={area} alCancelar={() => setF({ nuevo: "" })} alGuardar={(c) => ir(`/casos/${c.id}`)} />
                </div>
            )}

            <div className="table-filters qa-filters">
                <Busqueda id="cat-q" etiqueta="Buscar" valor={f.q} onChange={(q) => setF({ q })} placeholder="ID, título, pantalla o resultado esperado" />
                <Selector
                    id="cat-seccion"
                    etiqueta={area === "backend" ? "Sección" : "Caso de uso"}
                    valor={f.seccion}
                    opciones={[["", "Todas"], ...(secciones.datos?.secciones ?? []).map((s) => [s.seccion, s.titulo === s.seccion ? s.seccion : `${s.seccion} · ${s.titulo}`] as const)]}
                    onChange={(seccion) => setF({ seccion })}
                />
                <Selector id="cat-marcas" etiqueta="Marcas" valor={f.marcas} opciones={[["", "Todas"], ["mock", "Con mock"], ["ia_real", "IA real"], ["destructivo", "Solo local"]]} onChange={(marcas) => setF({ marcas })} />
                <Selector id="cat-archivados" etiqueta="Archivados" valor={f.archivados} opciones={[["", "Ocultar"], ["true", "Mostrar"]]} onChange={(archivados) => setF({ archivados })} />
            </div>

            {lista.error && <Alerta tipo="danger">{lista.error}</Alerta>}
            {!lista.datos && !lista.error && <Cargando />}
            {lista.datos && (
                <>
                    <p className="admin-muted small">{lista.datos.total} caso(s)</p>
                    {lista.datos.total === 0 ? (
                        <div className="qa-empty">
                            <Icon name="post_add" />
                            <p className="mb-0">Todavía no hay casos {area === "frontend" ? "del frontend" : "con estos filtros"}.</p>
                            {yo.permisos.escribir && (
                                <button type="button" className="btn btn-primary btn-sm" onClick={() => setF({ nuevo: "1" })}><Icon name="add" /> Crear el primero</button>
                            )}
                        </div>
                    ) : (
                        <div className="table-card">
                            <table className="table table-borderless admin-table">
                                <caption className="visually-hidden">Casos del área {AREA_TEXTO[area]}</caption>
                                <thead>
                                    <tr>
                                        <th scope="col">Caso</th>
                                        <th scope="col">{area === "backend" ? "Endpoint" : "Pantalla"}</th>
                                        <th scope="col">Severidad</th>
                                        <th scope="col">Marcas</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {lista.datos.casos.map((c) => (
                                        <tr key={c.id} className={c.activo ? undefined : "is-disabled"}>
                                            <td>
                                                <Link className="cell-title" href={`/casos/${c.id}`}>{c.titulo}</Link>
                                                <span className="cell-sub">
                                                    <code>{c.id}</code> · {c.manual ? c.seccion : `sección ${c.seccion}`}
                                                    {c.manual && " · manual"}
                                                    {!c.activo && " · archivado"}
                                                </span>
                                            </td>
                                            <td><code>{c.endpoint ?? "—"}</code></td>
                                            <td>{SEVERIDAD_TEXTO[c.severidad]}</td>
                                            <td><Marcas caso={c} /></td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </>
            )}
        </>
    );
}

type DetalleCaso = {
    caso: Caso;
    historial: { resultado_id: number; estado: EstadoResultado; notas: string | null; actualizado_por: string; actualizado_en: string; sesion: { id: number; titulo: string; entorno: Entorno; version: string | null } }[];
    bugs: { id: number; titulo: string; estado: EstadoBug; severidad: string; issue: { numero: number; url: string } | null; creado_en: string }[];
};

export function CasoDetalle({ id }: { id: string }) {
    const api = useApi();
    const yo = useYo();
    const toast = useToast();
    const [f, setF] = useFiltros({ editar: "" });
    const d = useDatos<DetalleCaso>(`/api/casos/${encodeURIComponent(id)}`);
    const [error, setError] = useState<string | null>(null);
    if (d.error) return <Alerta tipo="danger">{d.error}</Alerta>;
    if (!d.datos) return <Cargando />;
    const { caso, historial, bugs } = d.datos;

    async function archivar(activo: boolean) {
        try {
            await api(`/api/casos/${caso.id}/${activo ? "reactivar" : "archivar"}`, { method: "POST" });
            toast(activo ? `${caso.id} reactivado.` : `${caso.id} archivado: ya no aparece en sesiones nuevas.`);
            d.recargar();
        } catch (e) {
            setError((e as Error).message);
        }
    }

    return (
        <>
            <nav aria-label="Ruta" className="admin-breadcrumb">
                <Link href={`/casos?area=${caso.area}`}>Catálogo</Link> / {caso.id}
            </nav>
            <div className="admin-section-head">
                <div>
                    <h1 className="admin-h2">{caso.titulo}</h1>
                    <p className="admin-muted admin-section-lead">
                        <code>{caso.id}</code> · {AREA_TEXTO[caso.area]} · {caso.manual ? `caso de uso: ${caso.seccion}` : `sección ${caso.seccion}`} ·{" "}
                        {caso.endpoint ? <code>{caso.endpoint}</code> : "sin pantalla ni endpoint"} · severidad si falla: {SEVERIDAD_TEXTO[caso.severidad].toLowerCase()}
                        {caso.manual && caso.creado_por && ` · creado por @${caso.creado_por}`}
                    </p>
                </div>
                <div className="qa-actions">
                    <Marcas caso={caso} />
                    {!caso.activo && <span className="status status-disabled"><Icon name="inventory_2" /> Archivado</span>}
                    {caso.manual && yo.permisos.escribir && !f.editar && (
                        <>
                            <button type="button" className="btn btn-sm btn-outline-secondary" onClick={() => setF({ editar: "1" })}><Icon name="edit" /> Editar</button>
                            <button type="button" className="btn btn-sm btn-outline-secondary" onClick={() => void archivar(!caso.activo)}>
                                <Icon name={caso.activo ? "inventory_2" : "unarchive"} /> {caso.activo ? "Archivar" : "Reactivar"}
                            </button>
                        </>
                    )}
                </div>
            </div>
            {error && <Alerta tipo="danger">{error}</Alerta>}
            {!caso.manual && <p className="admin-note"><Icon name="info" /> Este caso viene del repo (<code>tester/casos</code>): para cambiarlo, editá su archivo y abrí un PR.</p>}

            {f.editar && caso.manual ? (
                <div className="mb-4">
                    <CasoFormulario caso={caso} area={caso.area} alCancelar={() => setF({ editar: "" })} alGuardar={() => { setF({ editar: "" }); d.recargar(); }} />
                </div>
            ) : (
                <div className="qa-two-cols">
                    <section className="qa-card" aria-labelledby="caso-def">
                        <h2 className="qa-card-title" id="caso-def">Definición</h2>
                        <div className="qa-detail is-single">
                            <div>
                                {caso.precondiciones.length > 0 && (
                                    <>
                                        <h3>Precondiciones</h3>
                                        <ul>{caso.precondiciones.map((p) => <li key={p}>{p}</li>)}</ul>
                                    </>
                                )}
                                <h3>Pasos</h3>
                                <ol>{caso.pasos.map((p) => <li key={p}>{p}</li>)}</ol>
                                {caso.datos && (<><h3>Datos de prueba</h3><p>{caso.datos}</p></>)}
                                <h3>Resultado esperado</h3>
                                <p className="mb-0">{caso.esperado}</p>
                            </div>
                        </div>
                    </section>

                    <section className="qa-card" aria-labelledby="caso-bugs">
                        <h2 className="qa-card-title" id="caso-bugs">Bugs</h2>
                        {bugs.length ? (
                            <ul className="qa-timeline">
                                {bugs.map((b) => (
                                    <li key={b.id}>
                                        <span className="meta"><Estado info={BUG_INFO[b.estado]} />{b.issue && <a href={b.issue.url} target="_blank" rel="noopener">#{b.issue.numero}</a>}</span>
                                        <p><Link href={`/bugs/${b.id}`}>{b.titulo}</Link></p>
                                    </li>
                                ))}
                            </ul>
                        ) : (
                            <p className="admin-muted mb-0">Sin bugs.</p>
                        )}
                    </section>
                </div>
            )}

            <div className="admin-section-head">
                <div>
                    <h2 className="admin-h2">Historial</h2>
                    <p className="admin-muted admin-section-lead">Qué dio este caso en cada sesión: sirve para ver regresiones entre versiones.</p>
                </div>
            </div>
            <div className="table-card">
                <table className="table table-borderless admin-table">
                    <caption className="visually-hidden">Resultados del caso en cada sesión</caption>
                    <thead>
                        <tr>
                            <th scope="col">Sesión</th>
                            <th scope="col">Entorno</th>
                            <th scope="col">Resultado</th>
                            <th scope="col">Notas</th>
                            <th scope="col">Registrado</th>
                        </tr>
                    </thead>
                    <tbody>
                        {!historial.length && (
                            <tr><td colSpan={5} className="admin-muted">Todavía no se probó en ninguna sesión.</td></tr>
                        )}
                        {historial.map((h) => (
                            <tr key={h.resultado_id}>
                                <td>
                                    <Link className="cell-title" href={`/sesiones/${h.sesion.id}`}>{h.sesion.titulo}</Link>
                                    <span className="cell-sub">{h.sesion.version ? `Versión ${h.sesion.version}` : "Sin versión"}</span>
                                </td>
                                <td>{ENTORNO_TEXTO[h.sesion.entorno]}</td>
                                <td><Estado info={RESULTADO_INFO[h.estado]} /></td>
                                <td className="qa-pre">{h.notas ?? "—"}</td>
                                <td>@{h.actualizado_por}<span className="cell-sub">{fecha(h.actualizado_en)}</span></td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </>
    );
}
