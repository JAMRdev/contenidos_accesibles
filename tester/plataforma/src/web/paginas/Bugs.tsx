// Bugs: listado con filtros y detalle con reporte, estado, historial y comentarios.
import { useState } from "react";
import { useApi, useDatos, useYo } from "../api";
import { Comentarios } from "../Comentarios";
import { Evidencias } from "../Evidencias";
import { PosiblesDuplicados, ReporteBug, VistaPreviaIssue } from "../Reporte";
import { Link, useFiltros } from "../ruta";
import type { Bug, BugDetalle, Entorno, EstadoBug, Seccion, Severidad } from "../tipos";
import { Alerta, BUG_INFO, Busqueda, Cargando, ENTORNO_TEXTO, Estado, fecha, Icon, Selector, SEVERIDAD_TEXTO, useToast } from "../ui";

const ESTADOS = Object.keys(BUG_INFO) as EstadoBug[];
const SEVERIDADES = Object.keys(SEVERIDAD_TEXTO) as Severidad[];

// Grupos de estados para el filtro (la API acepta varios separados por comas)
const FILTRO_ESTADO = [
    ["", "Todos"],
    ["abierto,en_curso,reabierto", "Abiertos (abierto, en curso, reabierto)"],
    ...ESTADOS.map((e) => [e, BUG_INFO[e].label] as const),
] as const;

const ORIGEN_TEXTO = { qa: "QA", github: "GitHub", sincronizacion: "Sincronización" } as const;

export function Bugs() {
    const [f, setF] = useFiltros({ q: "", estado: "", severidad: "", seccion: "", entorno: "", sesion: "", caso: "" });
    const consulta = new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString();
    const lista = useDatos<{ bugs: Bug[]; siguiente: number | null }>(`/api/bugs?limite=100&${consulta}`);
    const secciones = useDatos<{ secciones: Seccion[] }>("/api/casos/secciones");
    const estadoConocido = FILTRO_ESTADO.some(([v]) => v === f.estado);

    return (
        <>
            <div className="admin-section-head">
                <div>
                    <h2 className="admin-h2">Bugs</h2>
                    <p className="admin-muted admin-section-lead">
                        Los bugs se crean desde un caso que falló en una sesión. Publicados en GitHub, su estado se actualiza solo cuando cambia allá.
                    </p>
                </div>
            </div>

            <div className="table-filters qa-filters">
                <Busqueda id="bug-q" etiqueta="Buscar" valor={f.q} onChange={(q) => setF({ q })} placeholder="Título, descripción o ID de caso" />
                <Selector
                    id="bug-estado"
                    etiqueta="Estado"
                    valor={f.estado}
                    opciones={estadoConocido ? FILTRO_ESTADO : [...FILTRO_ESTADO, [f.estado, f.estado] as const]}
                    onChange={(estado) => setF({ estado })}
                />
                <Selector id="bug-sev" etiqueta="Severidad" valor={f.severidad} opciones={[["", "Todas"], ...SEVERIDADES.map((s) => [s, SEVERIDAD_TEXTO[s]] as const)]} onChange={(severidad) => setF({ severidad })} />
                <Selector
                    id="bug-seccion"
                    etiqueta="Sección"
                    valor={f.seccion}
                    opciones={[["", "Todas"], ...(secciones.datos?.secciones ?? []).map((s) => [s.seccion, `${s.seccion} · ${s.titulo}`] as const)]}
                    onChange={(seccion) => setF({ seccion })}
                />
                <Selector
                    id="bug-entorno"
                    etiqueta="Entorno"
                    valor={f.entorno}
                    opciones={[["", "Todos"], ...(Object.keys(ENTORNO_TEXTO) as Entorno[]).map((e) => [e, ENTORNO_TEXTO[e]] as const)]}
                    onChange={(entorno) => setF({ entorno })}
                />
            </div>
            {(f.sesion || f.caso) && (
                <p className="admin-note">
                    <Icon name="filter_alt" /> {f.sesion ? `Solo la sesión #${f.sesion}` : `Solo el caso ${f.caso}`}.{" "}
                    <button type="button" className="btn btn-link btn-sm p-0" onClick={() => setF({ sesion: "", caso: "" })}>Quitar filtro</button>
                </p>
            )}

            {lista.error && <Alerta tipo="danger">{lista.error}</Alerta>}
            {lista.cargando && !lista.datos && <Cargando />}
            {lista.datos && (
                <div className="table-card">
                    <table className="table table-borderless admin-table">
                        <caption className="visually-hidden">Bugs</caption>
                        <thead>
                            <tr>
                                <th scope="col">Bug</th>
                                <th scope="col">Caso</th>
                                <th scope="col">Severidad</th>
                                <th scope="col">Estado</th>
                                <th scope="col">GitHub</th>
                                <th scope="col">Creado por</th>
                            </tr>
                        </thead>
                        <tbody>
                            {!lista.datos.bugs.length && (
                                <tr>
                                    <td colSpan={6} className="admin-muted">No hay bugs con estos filtros.</td>
                                </tr>
                            )}
                            {lista.datos.bugs.map((b) => (
                                <tr key={b.id}>
                                    <td>
                                        <Link className="cell-title" href={`/bugs/${b.id}`}>{b.titulo}</Link>
                                        <span className="cell-sub">#{b.id}{b.entorno ? ` · ${ENTORNO_TEXTO[b.entorno]}` : ""}</span>
                                    </td>
                                    <td>
                                        <Link href={`/casos/${b.caso.id}`}><code>{b.caso.id}</code></Link>
                                        <span className="cell-sub">Sección {b.caso.seccion}</span>
                                    </td>
                                    <td>{SEVERIDAD_TEXTO[b.severidad]}</td>
                                    <td><Estado info={BUG_INFO[b.estado]} /></td>
                                    <td>{b.issue ? <a href={b.issue.url} target="_blank" rel="noopener">#{b.issue.numero}</a> : <span className="admin-muted">Sin publicar</span>}</td>
                                    <td>@{b.creado_por}<span className="cell-sub">{fecha(b.creado_en)}</span></td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </>
    );
}

export function BugDetallePagina({ id }: { id: string }) {
    const api = useApi();
    const yo = useYo();
    const toast = useToast();
    const bug = useDatos<BugDetalle>(`/api/bugs/${id}`);
    const [error, setError] = useState<string | null>(null);

    if (bug.error && !bug.datos) return <Alerta tipo="danger">{bug.error}</Alerta>;
    if (!bug.datos) return <Cargando />;
    const b = bug.datos;
    // Mismas reglas que el Worker: sin issue, borrador ↔ descartado; publicado, entre los estados publicados
    const posibles = b.issue ? ESTADOS.filter((e) => e !== "borrador") : (["borrador", "descartado"] as EstadoBug[]);

    async function cambiar(cambios: Record<string, unknown>, ok: string) {
        try {
            await api(`/api/bugs/${b.id}`, { method: "PATCH", body: JSON.stringify(cambios) });
            setError(null);
            toast(ok);
            bug.recargar();
        } catch (e) {
            setError((e as Error).message);
        }
    }

    return (
        <>
            <nav aria-label="Ruta" className="admin-breadcrumb">
                <Link href="/bugs">Bugs</Link> / #{b.id}
            </nav>
            <div className="admin-section-head">
                <div>
                    <h2 className="admin-h2">{b.titulo}</h2>
                    <p className="admin-muted admin-section-lead">
                        <Link href={`/casos/${b.caso.id}`}><code>{b.caso.id}</code></Link> {b.caso.titulo} · severidad {SEVERIDAD_TEXTO[b.severidad].toLowerCase()}
                        {b.sesion_id && <> · <Link href={`/sesiones/${b.sesion_id}`}>sesión #{b.sesion_id}</Link></>}
                        {b.entorno && ` (${ENTORNO_TEXTO[b.entorno]})`} · creado por @{b.creado_por} el {fecha(b.creado_en)}
                    </p>
                </div>
                <div className="qa-actions">
                    <Estado info={BUG_INFO[b.estado]} />
                    {yo.permisos.escribir && (
                        <Selector
                            id="bug-cambiar-estado"
                            etiqueta="Cambiar estado"
                            oculta
                            chico
                            valor={b.estado}
                            opciones={[[b.estado, `Cambiar estado…`] as const, ...posibles.filter((e) => e !== b.estado).map((e) => [e, BUG_INFO[e].label] as const)]}
                            onChange={(estado) => void cambiar({ estado }, `El bug pasó a ${BUG_INFO[estado].label}.`)}
                        />
                    )}
                </div>
            </div>
            {error && <Alerta tipo="danger">{error}</Alerta>}
            {!b.issue && b.estado === "borrador" && (
                <div className="alert alert-info" role="status">
                    <p className="mb-0">
                        <strong>Este bug todavía no está en GitHub.</strong> Por ahora queda como borrador en la plataforma: el botón para publicarlo como issue
                        (con la etiqueta <code>[QA - Issue]</code>) llega con #130.
                    </p>
                </div>
            )}

            <div className="qa-two-cols">
                <div>
                    <ReporteBug bug={b} alCambiar={bug.recargar} />

                    <section className="qa-card" aria-labelledby="bug-evidencias">
                        <h3 className="qa-card-title" id="bug-evidencias">Evidencias</h3>
                        <Evidencias destino={{ bug: b.id }} />
                    </section>

                    <section className="qa-card" aria-labelledby="bug-comentarios">
                        <h3 className="qa-card-title" id="bug-comentarios">Comentarios</h3>
                        <Comentarios ruta={`/api/bugs/${b.id}/comentarios`} iniciales={b.comentarios} conGithub />
                    </section>
                </div>

                <div>
                    {/* actualizado_en cambia con cada edición: la vista previa se vuelve a pedir */}
                    <VistaPreviaIssue bugId={b.id} version={b.actualizado_en} />
                    <PosiblesDuplicados bugId={b.id} />

                    <section className="qa-card" aria-labelledby="bug-github">
                        <h3 className="qa-card-title" id="bug-github">GitHub</h3>
                        {b.issue ? (
                            <dl className="status-facts">
                                <dt>Issue</dt>
                                <dd><a href={b.issue.url} target="_blank" rel="noopener">#{b.issue.numero}</a></dd>
                                <dt>Estado</dt>
                                <dd>{b.issue.estado ?? "—"}{b.issue.motivo ? ` (${b.issue.motivo})` : ""}</dd>
                                <dt>Tablero</dt>
                                <dd>{b.issue.tablero ?? "—"}</dd>
                                <dt>Sincronizado</dt>
                                <dd>{fecha(b.sincronizado_en)}</dd>
                            </dl>
                        ) : (
                            <p className="admin-muted mb-0">Todavía no se publicó. La publicación en GitHub llega con #130.</p>
                        )}
                    </section>

                    <section className="qa-card" aria-labelledby="bug-historial">
                        <h3 className="qa-card-title" id="bug-historial">Historial de estados</h3>
                        <ul className="qa-timeline">
                            {[...b.historial].reverse().map((h, i) => (
                                <li key={i}>
                                    <span className="meta">
                                        <span>{fecha(h.en)}</span>
                                        <span>{ORIGEN_TEXTO[h.origen]}{h.actor ? ` · @${h.actor}` : ""}</span>
                                    </span>
                                    <p>
                                        {h.de ? `${BUG_INFO[h.de].label} → ` : "Creado como "}
                                        <strong>{BUG_INFO[h.a].label}</strong>
                                        {h.nota ? ` · ${h.nota}` : ""}
                                    </p>
                                </li>
                            ))}
                        </ul>
                    </section>
                </div>
            </div>
        </>
    );
}
