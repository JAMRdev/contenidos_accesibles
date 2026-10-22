// Una sesión: avance, filtros y la tabla de casos donde QA registra cada resultado.
import { Fragment, useState } from "react";
import { useApi, useDatos, useYo } from "../api";
import { Comentarios } from "../Comentarios";
import { Evidencias } from "../Evidencias";
import { Link, useFiltros, useRuta } from "../ruta";
import type { Avance, Bug, Comentario, EstadoResultado, ResultadoCaso, Seccion, Sesion } from "../tipos";
import { Alerta, Busqueda, Cargando, ENTORNO_TEXTO, Estado, fecha, Icon, Kpi, Marcas, RESULTADO_INFO, Selector, SEVERIDAD_TEXTO, useToast } from "../ui";

type Detalle = { sesion: Sesion; avance: Avance; casos: ResultadoCaso[] };

const OPCIONES_RESULTADO = (["pendiente", "paso", "fallo", "bloqueado", "omitido"] as const).map((e) => [e, RESULTADO_INFO[e].label] as const);

function DetalleCaso({ sesion, fila, editable, alGuardar }: { sesion: Sesion; fila: ResultadoCaso; editable: boolean; alGuardar: (notas: string) => Promise<void> }) {
    const api = useApi();
    const { ir } = useRuta();
    const { caso, resultado } = fila;
    const [notas, setNotas] = useState(resultado?.notas ?? "");
    const comentarios = useDatos<{ comentarios: Comentario[] }>(resultado ? `/api/resultados/${resultado.id}/comentarios` : null);
    const [error, setError] = useState<string | null>(null);

    async function crearBug() {
        try {
            const bug = await api<Bug>("/api/bugs", {
                method: "POST",
                body: JSON.stringify({ caso_id: caso.id, sesion_id: sesion.id, titulo: caso.titulo, descripcion: notas ? `Obtenido: ${notas}` : "" }),
            });
            ir(`/bugs/${bug.id}`);
        } catch (e) {
            setError((e as Error).message);
        }
    }

    return (
        <div className="qa-detail">
            <div>
                {caso.precondiciones.length > 0 && (
                    <>
                        <h3>Precondiciones</h3>
                        <ul>{caso.precondiciones.map((p) => <li key={p}>{p}</li>)}</ul>
                    </>
                )}
                <h3>Pasos</h3>
                <ol>{caso.pasos.map((p) => <li key={p}>{p}</li>)}</ol>
                {caso.datos && (
                    <>
                        <h3>Datos de prueba</h3>
                        <p>{caso.datos}</p>
                    </>
                )}
                <h3>Resultado esperado</h3>
                <p>{caso.esperado}</p>
                <p className="admin-muted small">
                    Severidad si falla: {SEVERIDAD_TEXTO[caso.severidad]} · <Link href={`/casos/${caso.id}`}>Historial del caso</Link>
                </p>
            </div>
            <div>
                <div className="form-group">
                    <div className="form-label-container">
                        <label className="form-label" htmlFor={`notas-${caso.id}`}>Notas del resultado</label>
                    </div>
                    <textarea
                        className="form-control"
                        id={`notas-${caso.id}`}
                        value={notas}
                        disabled={!editable || !resultado}
                        placeholder={resultado ? "Qué obtuviste, código HTTP, detail…" : "Marcá primero el resultado del caso."}
                        onChange={(e) => setNotas(e.target.value)}
                    />
                </div>
                <div className="qa-actions mt-2">
                    {editable && resultado && (
                        <button type="button" className="btn btn-sm btn-outline-primary" onClick={() => void alGuardar(notas)} disabled={notas === (resultado.notas ?? "")}>
                            <Icon name="save" /> Guardar notas
                        </button>
                    )}
                    {editable && resultado?.estado === "fallo" && (
                        <button type="button" className="btn btn-sm btn-primary" onClick={() => void crearBug()}>
                            <Icon name="bug_report" /> Crear bug
                        </button>
                    )}
                </div>
                {error && <div className="mt-2"><Alerta tipo="danger">{error}</Alerta></div>}
                {resultado && (
                    <>
                        <h3 className="mt-3">Evidencias</h3>
                        <Evidencias destino={{ resultado: resultado.id }} />
                        <h3 className="mt-3">Comentarios</h3>
                        {comentarios.datos ? <Comentarios ruta={`/api/resultados/${resultado.id}/comentarios`} iniciales={comentarios.datos.comentarios} conGithub={false} /> : <Cargando />}
                    </>
                )}
            </div>
        </div>
    );
}

export function SesionDetalle({ id }: { id: string }) {
    const api = useApi();
    const yo = useYo();
    const toast = useToast();
    const [f, setF] = useFiltros({ q: "", seccion: "", resultado: "", marcas: "" });
    const consulta = new URLSearchParams(
        Object.entries({ q: f.q, seccion: f.seccion, resultado: f.resultado, ...(f.marcas ? { [f.marcas]: "true" } : {}) }).filter(([, v]) => v),
    ).toString();
    const detalle = useDatos<Detalle>(`/api/sesiones/${id}?${consulta}`);
    const secciones = useDatos<{ secciones: Seccion[] }>("/api/casos/secciones");
    const [abierto, setAbierto] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    if (detalle.error && !detalle.datos) return <Alerta tipo="danger">{detalle.error}</Alerta>;
    if (!detalle.datos) return <Cargando />;
    const { sesion, avance, casos } = detalle.datos;
    const editable = yo.permisos.escribir && sesion.estado === "abierta";

    async function registrar(fila: ResultadoCaso, estado: EstadoResultado | "pendiente", notas = fila.resultado?.notas ?? null) {
        try {
            const ruta = `/api/sesiones/${sesion.id}/resultados/${fila.caso.id}`;
            if (estado === "pendiente") await api(ruta, { method: "DELETE" });
            else await api(ruta, { method: "PUT", body: JSON.stringify({ estado, notas }) });
            setError(null);
            detalle.recargar();
            if (estado === "fallo") setAbierto(fila.caso.id);
        } catch (e) {
            setError((e as Error).message);
        }
    }

    async function cambiarEstadoSesion(accion: "cerrar" | "reabrir") {
        if (accion === "cerrar" && !window.confirm("Cerrada la sesión, ya no se pueden cambiar resultados (un admin puede reabrirla). ¿Cerrarla?")) return;
        try {
            await api(`/api/sesiones/${sesion.id}/${accion}`, { method: "POST" });
            toast(accion === "cerrar" ? "Sesión cerrada." : "Sesión reabierta.");
            detalle.recargar();
        } catch (e) {
            setError((e as Error).message);
        }
    }

    return (
        <>
            <nav aria-label="Ruta" className="admin-breadcrumb">
                <Link href="/sesiones">Sesiones</Link> / #{sesion.id}
            </nav>
            <div className="admin-section-head">
                <div>
                    <h2 className="admin-h2">{sesion.titulo}</h2>
                    <p className="admin-muted admin-section-lead">
                        {ENTORNO_TEXTO[sesion.entorno]} · <code>{sesion.url}</code> · {sesion.version ? `backend v${sesion.version}` : "sin versión"} · abierta por @{sesion.creada_por} el {fecha(sesion.creada_en)}
                        {sesion.estado === "cerrada" && ` · cerrada por @${sesion.cerrada_por} el ${fecha(sesion.cerrada_en)}`}
                    </p>
                    {sesion.notas && <p className="qa-pre admin-muted mt-1">{sesion.notas}</p>}
                </div>
                <div className="qa-actions">
                    <Link className="btn btn-outline-secondary" href={`/bugs?sesion=${sesion.id}`}>
                        <Icon name="bug_report" /> Bugs de la sesión
                    </Link>
                    {sesion.estado === "abierta" && yo.permisos.escribir && (
                        <button type="button" className="btn btn-outline-primary" onClick={() => void cambiarEstadoSesion("cerrar")}>
                            <Icon name="lock" /> Cerrar sesión
                        </button>
                    )}
                    {sesion.estado === "cerrada" && yo.permisos.administrar && (
                        <button type="button" className="btn btn-outline-primary" onClick={() => void cambiarEstadoSesion("reabrir")}>
                            <Icon name="lock_open" /> Reabrir
                        </button>
                    )}
                </div>
            </div>

            <div className="kpi-grid">
                <Kpi icon="checklist" label="Avance" valor={<>{avance.total - avance.pendiente}<span className="kpi-unit">/ {avance.total}</span></>} pie={`${avance.pendiente} pendientes`} />
                <Kpi icon="check_circle" label="Pasó" valor={avance.paso} />
                <Kpi icon="cancel" label="Falló" valor={avance.fallo} />
                <Kpi icon="block" label="Bloqueado u omitido" valor={avance.bloqueado + avance.omitido} />
            </div>

            <div className="table-filters qa-filters mt-4">
                <Busqueda id="sd-q" etiqueta="Buscar caso" valor={f.q} onChange={(q) => setF({ q })} placeholder="ID, título o endpoint" />
                <Selector
                    id="sd-seccion"
                    etiqueta="Sección"
                    valor={f.seccion}
                    opciones={[["", "Todas"], ...(secciones.datos?.secciones ?? []).map((s) => [s.seccion, `${s.seccion} · ${s.titulo}`] as const)]}
                    onChange={(seccion) => setF({ seccion })}
                />
                <Selector id="sd-resultado" etiqueta="Resultado" valor={f.resultado} opciones={[["", "Todos"], ...OPCIONES_RESULTADO]} onChange={(resultado) => setF({ resultado })} />
                <Selector
                    id="sd-marcas"
                    etiqueta="Marcas"
                    valor={f.marcas}
                    opciones={[["", "Todas"], ["mock", "Con mock"], ["ia_real", "IA real"], ["destructivo", "Solo local"]]}
                    onChange={(marcas) => setF({ marcas })}
                />
            </div>

            {error && <Alerta tipo="danger">{error}</Alerta>}

            <div className="table-card">
                <table className="table table-borderless admin-table">
                    <caption className="visually-hidden">Casos de la sesión y su resultado</caption>
                    <thead>
                        <tr>
                            <th scope="col">Caso</th>
                            <th scope="col">Endpoint</th>
                            <th scope="col">Marcas</th>
                            <th scope="col">Resultado</th>
                            <th scope="col">Registrado</th>
                            <th scope="col" className="col-actions"><span className="visually-hidden">Detalle</span></th>
                        </tr>
                    </thead>
                    <tbody>
                        {!casos.length && (
                            <tr>
                                <td colSpan={6} className="admin-muted">Ningún caso coincide con los filtros.</td>
                            </tr>
                        )}
                        {casos.map((fila) => {
                            const { caso, resultado } = fila;
                            const estado = resultado?.estado ?? "pendiente";
                            const expandido = abierto === caso.id;
                            return (
                                <Fragment key={caso.id}>
                                    <tr className={fila.aplica ? undefined : "is-disabled"}>
                                        <td>
                                            <span className="cell-title">{caso.titulo}</span>
                                            <span className="cell-sub"><code>{caso.id}</code></span>
                                        </td>
                                        <td><code>{caso.endpoint ?? "—"}</code></td>
                                        <td><Marcas caso={caso} /></td>
                                        <td>
                                            {editable && fila.aplica ? (
                                                <Selector id={`res-${caso.id}`} etiqueta={`Resultado de ${caso.id}`} oculta chico valor={estado} opciones={OPCIONES_RESULTADO} onChange={(e) => void registrar(fila, e)} />
                                            ) : fila.aplica ? (
                                                <Estado info={RESULTADO_INFO[estado]} />
                                            ) : (
                                                <span className="admin-muted small">No aplica en producción</span>
                                            )}
                                        </td>
                                        <td>{resultado ? <>@{resultado.actualizado_por}<span className="cell-sub">{fecha(resultado.actualizado_en)}</span></> : "—"}</td>
                                        <td>
                                            <div className="row-actions">
                                                <button
                                                    type="button"
                                                    className="btn btn-sm btn-outline-secondary btn-icon-only"
                                                    aria-expanded={expandido}
                                                    aria-label={`${expandido ? "Ocultar" : "Ver"} el detalle de ${caso.id}`}
                                                    title={expandido ? "Ocultar detalle" : "Ver detalle"}
                                                    onClick={() => setAbierto(expandido ? null : caso.id)}
                                                >
                                                    <Icon name={expandido ? "expand_less" : "expand_more"} />
                                                </button>
                                            </div>
                                        </td>
                                    </tr>
                                    {expandido && (
                                        <tr className="qa-detail-row">
                                            <td colSpan={6}>
                                                <DetalleCaso key={resultado?.id ?? "nuevo"} sesion={sesion} fila={fila} editable={editable && fila.aplica} alGuardar={(notas) => registrar(fila, estado, notas)} />
                                            </td>
                                        </tr>
                                    )}
                                </Fragment>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </>
    );
}
