// Evidencias de un resultado o un bug: subir archivos (elegir, arrastrar o pegar capturas), pegar
// texto (request, response, curl, consola) y verlas. Los archivos se piden con el token de sesión,
// así que se muestran desde un blob local (el bucket es privado).
import { useAuth } from "@clerk/react";
import { useCallback, useEffect, useRef, useState, type ClipboardEvent, type DragEvent, type FormEvent } from "react";
import { ApiError, useApi, useDatos, useYo } from "./api";
import { Link } from "./ruta";
import { Alerta, Cargando, fecha, Icon, Selector, useToast } from "./ui";

export type Evidencia = {
    id: number;
    resultado_id: number | null;
    bug_id: number | null;
    tipo: "imagen" | "video" | "texto";
    etiqueta: string | null;
    nombre: string;
    content_type: string;
    bytes: number;
    datos_ocultos: number;
    subida_por: string;
    subida_en: string;
    url: string;
};

const ETIQUETAS_TEXTO = ["Request", "Response", "curl", "Consola", "Otro"] as const;

// El navegador no siempre informa el tipo de .log, .har o .txt
const POR_EXTENSION: Record<string, string> = { log: "text/plain", txt: "text/plain", har: "application/har+json", json: "application/json" };

function tipoDe(archivo: File): string {
    const ext = archivo.name.split(".").pop()?.toLowerCase() ?? "";
    return POR_EXTENSION[ext] ?? archivo.type;
}

export function tamano(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** Contenido de una evidencia: URL de blob para imagen y video, texto para el resto. */
export function useContenido(ev: Evidencia | null) {
    const { getToken } = useAuth();
    const [contenido, setContenido] = useState<{ url?: string; texto?: string; error?: string } | null>(null);

    useEffect(() => {
        if (!ev) return;
        let url: string | undefined;
        let vigente = true;
        (async () => {
            try {
                const res = await fetch(`/api/evidencias/${ev.id}/archivo`, { headers: { Authorization: `Bearer ${await getToken()}` } });
                if (!res.ok) throw new Error(((await res.json().catch(() => ({}))) as { error?: string }).error ?? `Error ${res.status}`);
                if (ev.tipo === "texto") {
                    const texto = await res.text();
                    if (vigente) setContenido({ texto });
                } else {
                    url = URL.createObjectURL(await res.blob());
                    if (vigente) setContenido({ url });
                }
            } catch (e) {
                if (vigente) setContenido({ error: (e as Error).message });
            }
        })();
        return () => {
            vigente = false;
            if (url) URL.revokeObjectURL(url);
        };
    }, [ev, getToken]);

    return contenido;
}

export function Vista({ ev, completa = false }: { ev: Evidencia; completa?: boolean }) {
    const contenido = useContenido(ev);
    if (!contenido) return <Cargando texto="Cargando evidencia…" />;
    if (contenido.error) return <Alerta tipo="danger">{contenido.error}</Alerta>;
    if (ev.tipo === "imagen") return <img className="qa-evidence-media" src={contenido.url} alt={ev.etiqueta ? `${ev.etiqueta}: ${ev.nombre}` : ev.nombre} />;
    if (ev.tipo === "video") return <video className="qa-evidence-media" src={contenido.url} controls preload="metadata" />;
    let texto = contenido.texto ?? "";
    if (ev.content_type.includes("json")) {
        try {
            texto = JSON.stringify(JSON.parse(texto), null, 2);
        } catch {
            /* se muestra tal cual */
        }
    }
    return <pre className="qa-evidence-text">{completa || texto.length < 4000 ? texto : `${texto.slice(0, 4000)}\n…`}</pre>;
}

function Tarjeta({ ev, alBorrar }: { ev: Evidencia; alBorrar?: () => void }) {
    const toast = useToast();
    const copiar = async () => {
        await navigator.clipboard.writeText(`${location.origin}${ev.url}`);
        toast("Enlace copiado. Pide iniciar sesión para verlo.");
    };
    return (
        <article className="qa-evidence">
            <div className="qa-evidence-head">
                <div>
                    <span className="cell-title">{ev.etiqueta ?? ev.nombre}</span>
                    <span className="cell-sub">
                        {ev.nombre} · {tamano(ev.bytes)} · @{ev.subida_por} · {fecha(ev.subida_en)}
                    </span>
                </div>
                <div className="row-actions">
                    <Link className="btn btn-sm btn-outline-secondary btn-icon-only" href={ev.url} title="Abrir" aria-label={`Abrir ${ev.nombre}`}>
                        <Icon name="open_in_full" />
                    </Link>
                    <button type="button" className="btn btn-sm btn-outline-secondary btn-icon-only" title="Copiar enlace" aria-label={`Copiar el enlace de ${ev.nombre}`} onClick={() => void copiar()}>
                        <Icon name="link" />
                    </button>
                    {alBorrar && (
                        <button type="button" className="btn btn-sm btn-outline-danger btn-icon-only" title="Borrar" aria-label={`Borrar ${ev.nombre}`} onClick={alBorrar}>
                            <Icon name="delete" />
                        </button>
                    )}
                </div>
            </div>
            {ev.datos_ocultos > 0 && (
                <p className="admin-muted small mb-1">
                    <Icon name="visibility_off" /> Se ocultaron {ev.datos_ocultos} dato(s) sensible(s) antes de guardar.
                </p>
            )}
            <Vista ev={ev} />
        </article>
    );
}

export function Evidencias({ destino }: { destino: { resultado: number } | { bug: number } }) {
    const api = useApi();
    const { getToken } = useAuth();
    const yo = useYo();
    const toast = useToast();
    const consulta = "resultado" in destino ? `resultado=${destino.resultado}` : `bug=${destino.bug}`;
    const lista = useDatos<{ evidencias: Evidencia[] }>(`/api/evidencias?${consulta}`);
    const [subiendo, setSubiendo] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [texto, setTexto] = useState({ etiqueta: "Response" as (typeof ETIQUETAS_TEXTO)[number], valor: "" });
    const [arrastrando, setArrastrando] = useState(false);
    const entrada = useRef<HTMLInputElement>(null);
    const id = consulta.replace(/\W+/g, "-");

    const subir = useCallback(
        async (archivos: File[]) => {
            setError(null);
            for (const archivo of archivos) {
                setSubiendo(archivo.name);
                try {
                    const res = await fetch(`/api/evidencias?${consulta}`, {
                        method: "POST",
                        headers: { Authorization: `Bearer ${await getToken()}`, "Content-Type": tipoDe(archivo), "X-Nombre": encodeURIComponent(archivo.name) },
                        body: archivo,
                    });
                    const data = (await res.json().catch(() => ({}))) as Evidencia & { error?: string };
                    if (!res.ok) throw new ApiError(res.status, data.error ?? `Error ${res.status}`);
                    toast(data.datos_ocultos ? `${archivo.name}: se ocultaron ${data.datos_ocultos} dato(s) sensible(s).` : `${archivo.name} subido.`);
                } catch (e) {
                    setError(`${archivo.name}: ${(e as Error).message}`);
                }
            }
            setSubiendo(null);
            lista.recargar();
        },
        [consulta, getToken, lista, toast],
    );

    function alSoltar(ev: DragEvent) {
        ev.preventDefault();
        setArrastrando(false);
        void subir([...ev.dataTransfer.files]);
    }

    // Capturas pegadas con Ctrl+V (por ejemplo, desde la herramienta de recortes)
    function alPegar(ev: ClipboardEvent) {
        const imagenes = [...ev.clipboardData.files].filter((f) => f.type.startsWith("image/"));
        if (!imagenes.length) return;
        ev.preventDefault();
        void subir(imagenes.map((f, i) => new File([f], f.name && f.name !== "image.png" ? f.name : `captura-${Date.now()}-${i + 1}.png`, { type: f.type })));
    }

    async function guardarTexto(ev: FormEvent) {
        ev.preventDefault();
        try {
            const destinoId = "resultado" in destino ? { resultado_id: destino.resultado } : { bug_id: destino.bug };
            const r = await api<Evidencia>("/api/evidencias/texto", { method: "POST", body: JSON.stringify({ ...destinoId, etiqueta: texto.etiqueta, texto: texto.valor }) });
            toast(r.datos_ocultos ? `Guardado. Se ocultaron ${r.datos_ocultos} dato(s) sensible(s).` : "Texto guardado.");
            setTexto({ ...texto, valor: "" });
            setError(null);
            lista.recargar();
        } catch (e) {
            setError((e as Error).message);
        }
    }

    async function borrar(ev: Evidencia) {
        if (!window.confirm(`¿Borrar ${ev.nombre}? Los enlaces desde GitHub dejan de funcionar.`)) return;
        try {
            await api(`/api/evidencias/${ev.id}`, { method: "DELETE" });
            lista.recargar();
        } catch (e) {
            setError((e as Error).message);
        }
    }

    return (
        <div>
            {lista.datos ? (
                lista.datos.evidencias.length ? (
                    <div className="qa-evidence-grid">
                        {lista.datos.evidencias.map((e) => (
                            <Tarjeta key={e.id} ev={e} alBorrar={yo.permisos.administrar ? () => void borrar(e) : undefined} />
                        ))}
                    </div>
                ) : (
                    <p className="admin-muted">Todavía no hay evidencias.</p>
                )
            ) : (
                <Cargando />
            )}

            {yo.permisos.escribir && (
                <>
                    <div
                        className={`qa-dropzone mt-3${arrastrando ? " is-over" : ""}`}
                        onDragOver={(e) => {
                            e.preventDefault();
                            setArrastrando(true);
                        }}
                        onDragLeave={() => setArrastrando(false)}
                        onDrop={alSoltar}
                        onPaste={alPegar}
                        tabIndex={0}
                        aria-label="Zona para subir evidencias: arrastrá archivos o pegá una captura con Ctrl+V"
                    >
                        <Icon name="upload_file" />
                        <span>
                            Arrastrá archivos acá, pegá una captura con <kbd>Ctrl</kbd>+<kbd>V</kbd> o{" "}
                            <button type="button" className="btn btn-link p-0 align-baseline" onClick={() => entrada.current?.click()}>
                                elegilos
                            </button>
                            .
                        </span>
                        <span className="admin-muted small">Imágenes y GIF (hasta 10 MB), video MP4 o WebM (hasta 50 MB), JSON, HAR, log o texto (hasta 2 MB).</span>
                        <input
                            ref={entrada}
                            type="file"
                            hidden
                            multiple
                            accept="image/png,image/jpeg,image/webp,image/gif,video/mp4,video/webm,.json,.har,.log,.txt"
                            onChange={(e) => {
                                void subir([...(e.target.files ?? [])]);
                                e.target.value = "";
                            }}
                        />
                        {subiendo && (
                            <span className="admin-muted" aria-live="polite">
                                <span className="spinner-border spinner-border-sm me-1" aria-hidden="true" /> Subiendo {subiendo}…
                            </span>
                        )}
                    </div>

                    <form className="mt-3" onSubmit={guardarTexto}>
                        <div className="qa-paste">
                            <Selector
                                id={`${id}-etiqueta`}
                                etiqueta="Qué es"
                                valor={texto.etiqueta}
                                opciones={ETIQUETAS_TEXTO.map((e) => [e, e] as const)}
                                onChange={(etiqueta) => setTexto({ ...texto, etiqueta })}
                            />
                            <div className="form-group">
                                <div className="form-label-container">
                                    <label className="form-label" htmlFor={`${id}-texto`}>Pegar request, response, curl o consola</label>
                                </div>
                                <textarea className="form-control qa-mono" id={`${id}-texto`} rows={5} value={texto.valor} onChange={(e) => setTexto({ ...texto, valor: e.target.value })} />
                            </div>
                        </div>
                        <div className="qa-actions mt-2">
                            <button type="submit" className="btn btn-sm btn-outline-primary" disabled={!texto.valor.trim()}>
                                <Icon name="content_paste_go" /> Guardar texto
                            </button>
                            <span className="admin-muted small">Los tokens, cookies, claves y firmas de URL se ocultan antes de guardar.</span>
                        </div>
                    </form>

                    <p className="admin-note">
                        <Icon name="shield_person" /> Usá solo los datos de prueba de la guía (sección 3). Las capturas y los videos no se revisan solos: tapá cualquier dato
                        personal real antes de subirlos.
                    </p>
                </>
            )}
            {error && <Alerta tipo="danger">{error}</Alerta>}
        </div>
    );
}
