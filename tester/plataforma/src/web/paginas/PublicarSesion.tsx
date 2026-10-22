// Pantalla de publicación de una sesión: qué se va a crear o comentar en GitHub y el botón de publicar.
import { useState } from "react";
import { useApi, useDatos, useYo } from "../api";
import { Link } from "../ruta";
import { Alerta, Cargando, Icon, useToast } from "../ui";

type Plan = {
    bugs: { id: number; titulo: string; caso: string; listo: boolean; faltan: string[] }[];
    propuestas_cierre: { bug_id: number; issue: number; caso: string }[];
    ejecucion: { accion: "crear" | "actualizar"; numero: number | null };
    comentarios: number;
    repo: string;
    github_disponible: boolean;
};

type Resultado = {
    ejecucion: { numero: number; url: string };
    bugs: { bug_id: number; accion: string; issue: { numero: number; url: string } }[];
    omitidos: { id: number; faltan: string[] }[];
    propuestas_cierre: number[];
    comentarios: number;
    errores: string[];
    avisos: string[];
};

const ACCION = { creado: "creado", actualizado: "issue actualizado", vinculado: "comentado en el issue existente", duplicado: "duplicado: comentado en el issue existente", ya_publicado: "ya estaba publicado" } as const;

export function PublicarSesion({ id }: { id: string }) {
    const api = useApi();
    const yo = useYo();
    const toast = useToast();
    const plan = useDatos<Plan>(`/api/sesiones/${id}/publicacion`);
    const [publicando, setPublicando] = useState(false);
    const [resultado, setResultado] = useState<Resultado | null>(null);
    const [error, setError] = useState<string | null>(null);

    if (plan.error) return <Alerta tipo="danger">{plan.error}</Alerta>;
    if (!plan.datos) return <Cargando />;
    const p = plan.datos;
    const listos = p.bugs.filter((b) => b.listo);
    const incompletos = p.bugs.filter((b) => !b.listo);
    const base = p.repo ? `https://github.com/${p.repo}/issues` : "";

    async function publicar() {
        setPublicando(true);
        try {
            const r = await api<Resultado>(`/api/sesiones/${id}/publicar`, { method: "POST" });
            setResultado(r);
            setError(null);
            toast(`Sesión publicada: issue de ejecución #${r.ejecucion.numero}.`);
            plan.recargar();
        } catch (e) {
            setError((e as Error).message);
        } finally {
            setPublicando(false);
        }
    }

    return (
        <>
            <nav aria-label="Ruta" className="admin-breadcrumb">
                <Link href="/sesiones">Sesiones</Link> / <Link href={`/sesiones/${id}`}>#{id}</Link> / Publicar
            </nav>
            <div className="admin-section-head">
                <div>
                    <h1 className="admin-h2">Publicar en GitHub</h1>
                    <p className="admin-muted admin-section-lead">
                        Repo: <code>{p.repo}</code>. Nada se cierra solo: si un caso con bug abierto pasó, se propone cerrarlo con un comentario. Publicar de nuevo
                        no duplica.
                    </p>
                </div>
            </div>

            {!p.github_disponible && <Alerta tipo="warning">GitHub no está configurado: falta el secreto GITHUB_TOKEN del Worker.</Alerta>}

            {resultado && (
                <section className="qa-card mb-3" aria-labelledby="pub-resultado">
                    <h2 className="qa-card-title" id="pub-resultado">Resultado</h2>
                    <ul className="mb-2">
                        <li>
                            Issue de ejecución: <a href={resultado.ejecucion.url} target="_blank" rel="noopener">#{resultado.ejecucion.numero}</a>
                        </li>
                        {resultado.bugs.map((b) => (
                            <li key={b.bug_id}>
                                <Link href={`/bugs/${b.bug_id}`}>Bug #{b.bug_id}</Link>: {ACCION[b.accion as keyof typeof ACCION] ?? b.accion} ·{" "}
                                <a href={b.issue.url} target="_blank" rel="noopener">#{b.issue.numero}</a>
                            </li>
                        ))}
                        {resultado.propuestas_cierre.length > 0 && <li>Se propuso cerrar: {resultado.propuestas_cierre.map((n) => `#${n}`).join(", ")}</li>}
                        {resultado.comentarios > 0 && <li>{resultado.comentarios} comentario(s) enviados.</li>}
                        {resultado.omitidos.length > 0 && <li>{resultado.omitidos.length} bug(s) omitidos por estar incompletos.</li>}
                    </ul>
                    {resultado.errores.map((e) => <Alerta key={e} tipo="danger">{e}</Alerta>)}
                    {resultado.avisos.map((a) => <Alerta key={a} tipo="warning">{a}</Alerta>)}
                </section>
            )}

            <div className="qa-two-cols">
                <div className="qa-stack">
                    <section className="qa-card" aria-labelledby="pub-bugs">
                        <h2 className="qa-card-title" id="pub-bugs">Bugs en borrador</h2>
                        {!p.bugs.length && <p className="admin-muted mb-0">No hay bugs en borrador en esta sesión.</p>}
                        {listos.length > 0 && (
                            <>
                                <h3 className="qa-subtitle">Se publican ({listos.length})</h3>
                                <ul>
                                    {listos.map((b) => (
                                        <li key={b.id}><Link href={`/bugs/${b.id}`}>{b.titulo}</Link> <code>{b.caso}</code></li>
                                    ))}
                                </ul>
                            </>
                        )}
                        {incompletos.length > 0 && (
                            <>
                                <h3 className="qa-subtitle">Se omiten por incompletos ({incompletos.length})</h3>
                                <ul>
                                    {incompletos.map((b) => (
                                        <li key={b.id}>
                                            <Link href={`/bugs/${b.id}`}>{b.titulo}</Link> · falta {b.faltan.join(", ")}
                                        </li>
                                    ))}
                                </ul>
                            </>
                        )}
                        <p className="admin-muted small mb-0">Si ya hay un issue abierto del mismo caso, se comenta en él en lugar de crear otro.</p>
                    </section>
                </div>
                <div className="qa-stack">
                    <section className="qa-card" aria-labelledby="pub-otros">
                        <h2 className="qa-card-title" id="pub-otros">Además</h2>
                        <ul className="mb-0">
                            <li>
                                Issue de ejecución:{" "}
                                {p.ejecucion.accion === "crear" ? "se crea" : <>se actualiza <a href={`${base}/${p.ejecucion.numero}`} target="_blank" rel="noopener">#{p.ejecucion.numero}</a></>}
                            </li>
                            <li>
                                Propuestas de cierre:{" "}
                                {p.propuestas_cierre.length ? p.propuestas_cierre.map((x) => `#${x.issue} (${x.caso})`).join(", ") : "ninguna"}
                            </li>
                            <li>Comentarios para GitHub: {p.comentarios}</li>
                        </ul>
                    </section>
                </div>
            </div>

            {error && <Alerta tipo="danger">{error}</Alerta>}
            {yo.permisos.escribir && (
                <div className="qa-actions mt-3">
                    <button type="button" className="btn btn-primary" disabled={publicando || !p.github_disponible} onClick={() => void publicar()}>
                        {publicando ? <span className="spinner-border spinner-border-sm me-1" aria-hidden="true" /> : <Icon name="publish" />} Publicar en GitHub
                    </button>
                    <Link className="btn btn-outline-secondary" href={`/sesiones/${id}`}>Volver a la sesión</Link>
                </div>
            )}
        </>
    );
}
