// Catálogo de casos: listado con filtros y detalle con su historial entre sesiones y sus bugs.
import { useDatos } from "../api";
import { Link, useFiltros } from "../ruta";
import type { Caso, EstadoBug, EstadoResultado, Seccion } from "../tipos";
import { Alerta, BUG_INFO, Busqueda, Cargando, ENTORNO_TEXTO, Estado, fecha, Marcas, RESULTADO_INFO, Selector, SEVERIDAD_TEXTO } from "../ui";
import type { Entorno } from "../tipos";

export function Casos() {
    const [f, setF] = useFiltros({ q: "", seccion: "", marcas: "" });
    const consulta = new URLSearchParams(Object.entries({ q: f.q, seccion: f.seccion, ...(f.marcas ? { [f.marcas]: "true" } : {}) }).filter(([, v]) => v)).toString();
    const lista = useDatos<{ secciones: Seccion[]; total: number; casos: Caso[] }>(`/api/casos?${consulta}`);
    const secciones = useDatos<{ secciones: Seccion[] }>("/api/casos/secciones");

    return (
        <>
            <div className="admin-section-head">
                <div>
                    <h2 className="admin-h2">Catálogo de casos</h2>
                    <p className="admin-muted admin-section-lead">
                        Los casos viven en el repo (<code>tester/casos/</code>) y llegan con cada deploy. Para cambiar uno, editá su archivo y abrí un PR.
                    </p>
                </div>
            </div>
            <div className="table-filters qa-filters">
                <Busqueda id="cat-q" etiqueta="Buscar" valor={f.q} onChange={(q) => setF({ q })} placeholder="ID, título, endpoint o resultado esperado" />
                <Selector
                    id="cat-seccion"
                    etiqueta="Sección"
                    valor={f.seccion}
                    opciones={[["", "Todas"], ...(secciones.datos?.secciones ?? []).map((s) => [s.seccion, `${s.seccion} · ${s.titulo}`] as const)]}
                    onChange={(seccion) => setF({ seccion })}
                />
                <Selector
                    id="cat-marcas"
                    etiqueta="Marcas"
                    valor={f.marcas}
                    opciones={[["", "Todas"], ["mock", "Con mock"], ["ia_real", "IA real"], ["destructivo", "Solo local"]]}
                    onChange={(marcas) => setF({ marcas })}
                />
            </div>
            {lista.error && <Alerta tipo="danger">{lista.error}</Alerta>}
            {!lista.datos && !lista.error && <Cargando />}
            {lista.datos && (
                <>
                    <p className="admin-muted small">{lista.datos.total} casos</p>
                    <div className="table-card">
                        <table className="table table-borderless admin-table">
                            <caption className="visually-hidden">Casos del catálogo</caption>
                            <thead>
                                <tr>
                                    <th scope="col">Caso</th>
                                    <th scope="col">Endpoint</th>
                                    <th scope="col">Severidad</th>
                                    <th scope="col">Marcas</th>
                                </tr>
                            </thead>
                            <tbody>
                                {lista.datos.casos.map((c) => (
                                    <tr key={c.id}>
                                        <td>
                                            <Link className="cell-title" href={`/casos/${c.id}`}>{c.titulo}</Link>
                                            <span className="cell-sub"><code>{c.id}</code> · sección {c.seccion}</span>
                                        </td>
                                        <td><code>{c.endpoint ?? "—"}</code></td>
                                        <td>{SEVERIDAD_TEXTO[c.severidad]}</td>
                                        <td><Marcas caso={c} /></td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
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
    const d = useDatos<DetalleCaso>(`/api/casos/${encodeURIComponent(id)}`);
    if (d.error) return <Alerta tipo="danger">{d.error}</Alerta>;
    if (!d.datos) return <Cargando />;
    const { caso, historial, bugs } = d.datos;

    return (
        <>
            <nav aria-label="Ruta" className="admin-breadcrumb">
                <Link href="/casos">Catálogo</Link> / {caso.id}
            </nav>
            <div className="admin-section-head">
                <div>
                    <h2 className="admin-h2">{caso.titulo}</h2>
                    <p className="admin-muted admin-section-lead">
                        <code>{caso.id}</code> · sección {caso.seccion} · {caso.endpoint ? <code>{caso.endpoint}</code> : "flujo de punta a punta"} · severidad si falla: {SEVERIDAD_TEXTO[caso.severidad].toLowerCase()}
                    </p>
                </div>
                <Marcas caso={caso} />
            </div>

            <div className="qa-two-cols">
                <section className="qa-card" aria-labelledby="caso-def">
                    <h3 className="qa-card-title" id="caso-def">Definición</h3>
                    <div className="qa-detail" style={{ gridTemplateColumns: "1fr", padding: 0 }}>
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
                    <h3 className="qa-card-title" id="caso-bugs">Bugs</h3>
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
                                    <span className="cell-sub">{h.sesion.version ? `Backend v${h.sesion.version}` : "Sin versión"}</span>
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
