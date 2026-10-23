// Comentarios de un resultado o de un bug, con la opción de mandarlos también a GitHub.
import { useState, type FormEvent } from "react";
import { useApi, useYo } from "./api";
import type { Comentario } from "./tipos";
import { Alerta, fecha, Icon } from "./ui";

export function Comentarios({ ruta, iniciales, conGithub }: { ruta: string; iniciales: Comentario[]; conGithub: boolean }) {
    const api = useApi();
    const yo = useYo();
    const [lista, setLista] = useState(iniciales);
    const [texto, setTexto] = useState("");
    const [enGithub, setEnGithub] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [enviando, setEnviando] = useState(false);
    const id = ruta.replace(/\W+/g, "-");

    async function enviar(ev: FormEvent) {
        ev.preventDefault();
        setEnviando(true);
        try {
            const r = await api<{ comentarios: Comentario[] }>(ruta, { method: "POST", body: JSON.stringify({ texto, en_github: conGithub && enGithub }) });
            setLista(r.comentarios);
            setTexto("");
            setEnGithub(false);
            setError(null);
        } catch (e) {
            setError((e as Error).message);
        } finally {
            setEnviando(false);
        }
    }

    return (
        <div>
            {lista.length ? (
                <ul className="qa-timeline">
                    {lista.map((c) => (
                        <li key={c.id}>
                            <span className="meta">
                                <strong>@{c.autor}</strong>
                                <span>{fecha(c.creado_en)}</span>
                                {c.origen === "github" && (
                                    <span>
                                        <Icon name="code" /> desde GitHub
                                    </span>
                                )}
                                {c.en_github && c.origen === "plataforma" && (
                                    <span>
                                        <Icon name={c.publicado_en_github ? "cloud_done" : "cloud_upload"} />
                                        {c.publicado_en_github ? "publicado en GitHub" : "se publica en GitHub"}
                                    </span>
                                )}
                            </span>
                            <p>{c.texto}</p>
                        </li>
                    ))}
                </ul>
            ) : (
                <p className="admin-muted">Todavía no hay comentarios.</p>
            )}

            {yo.permisos.escribir && (
                <form className="mt-3" onSubmit={enviar}>
                    <div className="form-group">
                        <div className="form-label-container">
                            <label className="form-label" htmlFor={`${id}-texto`}>
                                Nuevo comentario
                            </label>
                        </div>
                        <textarea className="form-control" id={`${id}-texto`} value={texto} onChange={(e) => setTexto(e.target.value)} />
                    </div>
                    <div className="qa-actions mt-2">
                        {conGithub && (
                            <div className="form-check">
                                <input className="form-check-input" type="checkbox" id={`${id}-github`} checked={enGithub} onChange={(e) => setEnGithub(e.target.checked)} />
                                <label className="form-check-label" htmlFor={`${id}-github`}>
                                    Publicarlo también en el issue de GitHub
                                </label>
                            </div>
                        )}
                        <button type="submit" className="btn btn-sm btn-primary" disabled={enviando || !texto.trim()}>
                            <Icon name="send" /> Comentar
                        </button>
                    </div>
                    {error && (
                        <div className="mt-2">
                            <Alerta tipo="danger">{error}</Alerta>
                        </div>
                    )}
                </form>
            )}
        </div>
    );
}
