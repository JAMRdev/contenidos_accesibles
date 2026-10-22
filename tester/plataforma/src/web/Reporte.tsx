// Reporte de un bug (#129): formulario, vista previa del issue y posibles duplicados.
import { useState, type FormEvent } from "react";
import { useApi, useDatos, useYo } from "./api";
import { Link } from "./ruta";
import type { BugDetalle, Duplicados, Severidad, VistaPrevia } from "./tipos";
import { Alerta, BUG_INFO, Cargando, Estado, Icon, Selector, SEVERIDAD_TEXTO, useToast } from "./ui";

type Formulario = { titulo: string; severidad: Severidad; pasos: string; esperado: string; obtenido: string; url: string; version: string; descripcion: string };

function Campo({ id, etiqueta, ayuda, children }: { id: string; etiqueta: string; ayuda?: string; children: React.ReactNode }) {
    return (
        <div className="form-group mb-3">
            <div className="form-label-container">
                <label className="form-label" htmlFor={id}>{etiqueta}</label>
                {ayuda && <p className="form-label-description admin-muted small mb-0">{ayuda}</p>}
            </div>
            {children}
        </div>
    );
}

function Lectura({ titulo, texto, mono = false }: { titulo: string; texto: string | null; mono?: boolean }) {
    return (
        <>
            <h4 className="qa-subtitle">{titulo}</h4>
            {texto?.trim() ? <p className={`qa-pre${mono ? " qa-evidence-text" : ""}`}>{texto}</p> : <p className="admin-muted">Sin completar.</p>}
        </>
    );
}

export function ReporteBug({ bug, alCambiar }: { bug: BugDetalle; alCambiar: () => void }) {
    const api = useApi();
    const yo = useYo();
    const toast = useToast();
    const [form, setForm] = useState<Formulario | null>(null);
    const [error, setError] = useState<string | null>(null);
    const r = bug.reporte;

    const editar = () =>
        setForm({ titulo: bug.titulo, severidad: bug.severidad, pasos: r.pasos, esperado: r.esperado, obtenido: r.obtenido, url: r.url ?? "", version: r.version ?? "", descripcion: bug.descripcion });

    async function guardar(ev: FormEvent) {
        ev.preventDefault();
        if (!form) return;
        try {
            await api(`/api/bugs/${bug.id}`, { method: "PATCH", body: JSON.stringify(form) });
            setForm(null);
            setError(null);
            toast("Reporte guardado.");
            alCambiar();
        } catch (e) {
            setError((e as Error).message);
        }
    }

    const set = (campo: keyof Formulario) => (e: { target: { value: string } }) => setForm((f) => (f ? { ...f, [campo]: e.target.value } : f));

    return (
        <section className="qa-card" aria-labelledby="bug-reporte">
            <div className="qa-actions" style={{ justifyContent: "space-between" }}>
                <h3 className="qa-card-title" id="bug-reporte">Reporte</h3>
                {yo.permisos.escribir && !form && (
                    <button type="button" className="btn btn-sm btn-outline-secondary" onClick={editar}>
                        <Icon name="edit" /> Editar
                    </button>
                )}
            </div>
            {form ? (
                <form onSubmit={guardar}>
                    <Campo id="rep-titulo" etiqueta="Título" ayuda="Se publica como [QA - Issue] <caso> · <título>.">
                        <input className="form-control" id="rep-titulo" value={form.titulo} onChange={set("titulo")} />
                    </Campo>
                    <div className="form-grid form-grid-3">
                        <Selector id="rep-sev" etiqueta="Severidad" valor={form.severidad} opciones={(Object.keys(SEVERIDAD_TEXTO) as Severidad[]).map((s) => [s, SEVERIDAD_TEXTO[s]] as const)} onChange={(severidad) => setForm({ ...form, severidad })} />
                        <Campo id="rep-url" etiqueta="URL probada">
                            <input className="form-control" id="rep-url" value={form.url} onChange={set("url")} />
                        </Campo>
                        <Campo id="rep-version" etiqueta="Versión del backend">
                            <input className="form-control" id="rep-version" value={form.version} onChange={set("version")} />
                        </Campo>
                    </div>
                    <Campo id="rep-pasos" etiqueta="Pasos para reproducir" ayuda="Uno por línea: se numeran solos.">
                        <textarea className="form-control" id="rep-pasos" rows={5} value={form.pasos} onChange={set("pasos")} />
                    </Campo>
                    <Campo id="rep-esperado" etiqueta="Resultado esperado">
                        <textarea className="form-control" id="rep-esperado" rows={3} value={form.esperado} onChange={set("esperado")} />
                    </Campo>
                    <Campo id="rep-obtenido" etiqueta="Resultado obtenido" ayuda="Código HTTP y detail. Si pegás varias líneas, se publican como bloque de código.">
                        <textarea className="form-control qa-mono" id="rep-obtenido" rows={5} value={form.obtenido} onChange={set("obtenido")} />
                    </Campo>
                    <Campo id="rep-notas" etiqueta="Notas (opcional)">
                        <textarea className="form-control" id="rep-notas" rows={3} value={form.descripcion} onChange={set("descripcion")} />
                    </Campo>
                    {error && <Alerta tipo="danger">{error}</Alerta>}
                    <div className="qa-actions">
                        <button type="submit" className="btn btn-primary btn-sm"><Icon name="save" /> Guardar</button>
                        <button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => setForm(null)}>Cancelar</button>
                    </div>
                </form>
            ) : (
                <>
                    <dl className="status-facts mb-3">
                        <dt>Severidad</dt>
                        <dd>{SEVERIDAD_TEXTO[bug.severidad]}</dd>
                        <dt>URL probada</dt>
                        <dd>{r.url ? <code>{r.url}</code> : "—"}</dd>
                        <dt>Versión</dt>
                        <dd>{r.version ?? "—"}</dd>
                    </dl>
                    <Lectura titulo="Pasos para reproducir" texto={r.pasos} />
                    <Lectura titulo="Resultado esperado" texto={r.esperado} />
                    <Lectura titulo="Resultado obtenido" texto={r.obtenido} mono />
                    {bug.descripcion.trim() && <Lectura titulo="Notas" texto={bug.descripcion} />}
                </>
            )}
        </section>
    );
}

type ResultadoPublicacion = { accion: "creado" | "vinculado" | "duplicado" | "ya_publicado"; issue: { numero: number; url: string }; comentarios: number; avisos: string[] };

const MENSAJE_PUBLICACION: Record<ResultadoPublicacion["accion"], (n: number) => string> = {
    creado: (n) => `Publicado en GitHub como #${n}.`,
    vinculado: (n) => `Ya había un issue abierto de este caso (#${n}): se comentó ahí y el bug quedó vinculado.`,
    duplicado: (n) => `Ya había un bug abierto de este caso: se comentó en #${n} y este quedó descartado como duplicado.`,
    ya_publicado: (n) => `El bug ya estaba publicado en #${n}.`,
};

/** Publicar el bug en GitHub (o enviar sus comentarios pendientes si ya está publicado). */
export function PublicarBug({ bug, alPublicar }: { bug: BugDetalle; alPublicar: () => void }) {
    const api = useApi();
    const yo = useYo();
    const toast = useToast();
    const previa = useDatos<VistaPrevia>(bug.issue ? null : `/api/bugs/${bug.id}/vista-previa?v=${encodeURIComponent(bug.actualizado_en)}`);
    const [publicando, setPublicando] = useState(false);
    const [resultado, setResultado] = useState<ResultadoPublicacion | null>(null);
    const [error, setError] = useState<string | null>(null);
    const pendientes = bug.comentarios.filter((c) => c.en_github && !c.publicado_en_github && c.origen === "plataforma").length;

    async function publicar() {
        if (!bug.issue && !window.confirm(`Se va a publicar en ${previa.datos?.repo ?? "GitHub"} (o a comentar en el issue abierto del mismo caso). ¿Seguir?`)) return;
        setPublicando(true);
        try {
            const r = await api<ResultadoPublicacion>(`/api/bugs/${bug.id}/publicar`, { method: "POST" });
            setResultado(r);
            setError(null);
            toast(MENSAJE_PUBLICACION[r.accion](r.issue.numero));
            alPublicar();
        } catch (e) {
            setError((e as Error).message);
        } finally {
            setPublicando(false);
        }
    }

    const resultadoAlerta = resultado && (
        <div className={`alert alert-${resultado.accion === "duplicado" ? "warning" : "success"}`} role="status">
            <p className="mb-0">
                {MENSAJE_PUBLICACION[resultado.accion](resultado.issue.numero)}{" "}
                <a href={resultado.issue.url} target="_blank" rel="noopener">Abrir en GitHub</a>
                {resultado.comentarios > 0 && ` · ${resultado.comentarios} comentario(s) enviado(s).`}
            </p>
            {resultado.avisos.map((a) => <p key={a} className="mb-0 mt-1 small">{a}</p>)}
        </div>
    );

    if (bug.issue) {
        return (
            <>
                {resultadoAlerta}
                {pendientes > 0 && yo.permisos.escribir && (
                    <div className="alert alert-info" role="status">
                        <p className="mb-2">Hay {pendientes} comentario(s) marcados para GitHub que todavía no se enviaron al issue #{bug.issue.numero}.</p>
                        <button type="button" className="btn btn-sm btn-primary" disabled={publicando} onClick={() => void publicar()}>
                            <Icon name="send" /> Enviar a GitHub
                        </button>
                    </div>
                )}
                {error && <Alerta tipo="danger">{error}</Alerta>}
            </>
        );
    }
    if (bug.estado !== "borrador") return null;

    const faltan = previa.datos?.faltan ?? [];
    return (
        <div className="alert alert-info" role="status">
            <p className="mb-2">
                <strong>Este bug todavía no está en GitHub.</strong>{" "}
                {faltan.length ? `Para publicarlo falta completar: ${faltan.join(", ")}.` : `Está listo para publicarse en ${previa.datos?.repo ?? "GitHub"} con la etiqueta [QA - Issue].`}
            </p>
            {yo.permisos.escribir && (
                <button type="button" className="btn btn-sm btn-primary" disabled={publicando || !previa.datos || faltan.length > 0} onClick={() => void publicar()}>
                    {publicando ? <span className="spinner-border spinner-border-sm me-1" aria-hidden="true" /> : <Icon name="publish" />} Publicar en GitHub
                </button>
            )}
            {error && <div className="mt-2"><Alerta tipo="danger">{error}</Alerta></div>}
        </div>
    );
}

export function VistaPreviaIssue({ bugId, version }: { bugId: number; version: string }) {
    const previa = useDatos<VistaPrevia>(`/api/bugs/${bugId}/vista-previa?v=${encodeURIComponent(version)}`);
    const [abierta, setAbierta] = useState(false);
    if (previa.error) return <Alerta tipo="danger">{previa.error}</Alerta>;
    if (!previa.datos) return <Cargando />;
    const p = previa.datos;
    return (
        <section className="qa-card" aria-labelledby="bug-previa">
            <h3 className="qa-card-title" id="bug-previa">Vista previa del issue</h3>
            {p.faltan.length ? (
                <Alerta tipo="warning">Para publicarlo falta completar: {p.faltan.join(", ")}.</Alerta>
            ) : (
                <Alerta tipo="success">El reporte está completo y listo para publicar.</Alerta>
            )}
            <dl className="status-facts mb-2">
                <dt>Repo</dt>
                <dd><code>{p.repo}</code></dd>
                <dt>Título</dt>
                <dd>{p.titulo}</dd>
                <dt>Etiquetas</dt>
                <dd>{p.etiquetas.map((e) => <span key={e} className="badge badge-s-info badge-sm me-1">{e}</span>)}</dd>
            </dl>
            <button type="button" className="btn btn-sm btn-outline-secondary" aria-expanded={abierta} onClick={() => setAbierta(!abierta)}>
                <Icon name={abierta ? "expand_less" : "expand_more"} /> {abierta ? "Ocultar" : "Ver"} el cuerpo en Markdown
            </button>
            {abierta && <pre className="qa-evidence-text mt-2">{p.cuerpo}</pre>}
        </section>
    );
}

export function PosiblesDuplicados({ bugId }: { bugId: number }) {
    const d = useDatos<Duplicados>(`/api/bugs/${bugId}/duplicados`);
    if (d.error) return <Alerta tipo="danger">{d.error}</Alerta>;
    if (!d.datos) return <Cargando />;
    const { plataforma, github } = d.datos;
    const hay = plataforma.length > 0 || github.issues.length > 0;
    return (
        <section className="qa-card" aria-labelledby="bug-duplicados">
            <h3 className="qa-card-title" id="bug-duplicados">Posibles duplicados</h3>
            {!hay && <p className="admin-muted">No hay otros bugs activos de este caso{github.disponible ? " ni issues abiertos en GitHub" : ""}.</p>}
            {plataforma.length > 0 && (
                <>
                    <h4 className="qa-subtitle">En la plataforma</h4>
                    <ul className="qa-timeline mb-3">
                        {plataforma.map((b) => (
                            <li key={b.id}>
                                <span className="meta"><Estado info={BUG_INFO[b.estado]} />{b.issue && <a href={b.issue.url} target="_blank" rel="noopener">#{b.issue.numero}</a>}</span>
                                <p><Link href={`/bugs/${b.id}`}>{b.titulo}</Link></p>
                            </li>
                        ))}
                    </ul>
                </>
            )}
            {github.issues.length > 0 && (
                <>
                    <h4 className="qa-subtitle">Issues abiertos en GitHub</h4>
                    <ul className="qa-timeline mb-3">
                        {github.issues.map((i) => (
                            <li key={i.numero}>
                                <p><a href={i.url} target="_blank" rel="noopener">#{i.numero} {i.titulo}</a></p>
                            </li>
                        ))}
                    </ul>
                </>
            )}
            {hay && <p className="admin-muted small mb-0">Al publicar, si hay un issue abierto de este caso, la plataforma comenta en él en lugar de crear otro.</p>}
            {!github.disponible && <p className="admin-note mb-0"><Icon name="info" /> GitHub no está configurado todavía: solo se buscan duplicados en la plataforma.</p>}
            {github.error && <Alerta tipo="warning">No se pudo buscar en GitHub: {github.error}</Alerta>}
        </section>
    );
}
