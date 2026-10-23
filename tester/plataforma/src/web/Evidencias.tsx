// Evidencias de un resultado o un bug: una fila por evidencia y, al tocarla, un modal que la muestra
// como corresponde (código formateado, video, imagen o PDF). Los archivos se suben solos al soltarlos,
// elegirlos o pegarlos. El bucket es privado: los archivos se piden con el token de sesión.
import { useAuth } from "@clerk/react";
import { useCallback, useEffect, useRef, useState, type ClipboardEvent, type DragEvent, type FormEvent } from "react";
import { ApiError, useApi, useDatos, useYo } from "./api";
import { Link } from "./ruta";
import { Alerta, Cargando, fecha, Icon, Selector, useConfirmar, useToast } from "./ui";

export type Evidencia = {
    id: number;
    resultado_id: number | null;
    bug_id: number | null;
    tipo: "imagen" | "video" | "texto" | "pdf";
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

// El navegador no siempre informa el tipo de .log, .har, .txt o .json
const POR_EXTENSION: Record<string, string> = { log: "text/plain", txt: "text/plain", har: "application/har+json", json: "application/json", pdf: "application/pdf" };

const ICONO: Record<Evidencia["tipo"], string> = { imagen: "image", video: "movie", texto: "description", pdf: "picture_as_pdf" };

function tipoDe(archivo: File): string {
    const ext = archivo.name.split(".").pop()?.toLowerCase() ?? "";
    return POR_EXTENSION[ext] ?? archivo.type;
}

function icono(ev: Evidencia): string {
    return ev.content_type.includes("json") ? "data_object" : ICONO[ev.tipo];
}

export function tamano(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** Contenido de una evidencia: URL de blob para imagen, video y PDF; texto para el resto. */
export function useContenido(ev: Evidencia | null) {
    const { getToken } = useAuth();
    const [contenido, setContenido] = useState<{ url?: string; texto?: string; error?: string } | null>(null);

    useEffect(() => {
        if (!ev) return;
        let url: string | undefined;
        let vigente = true;
        setContenido(null);
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

/** Código con números de línea; el JSON se muestra formateado. */
function Codigo({ texto, json }: { texto: string; json: boolean }) {
    const [ajustar, setAjustar] = useState(true);
    let mostrado = texto;
    if (json) {
        try {
            mostrado = JSON.stringify(JSON.parse(texto), null, 2);
        } catch {
            /* se muestra tal cual */
        }
    }
    const lineas = mostrado.replace(/\n$/, "").split("\n");
    return (
        <div>
            <div className="qa-actions mb-2">
                <span className="admin-muted small">{lineas.length} línea(s){json ? " · JSON" : ""}</span>
                <button type="button" className="btn btn-sm btn-outline-secondary" onClick={() => setAjustar(!ajustar)} aria-pressed={ajustar}>
                    <Icon name="wrap_text" /> {ajustar ? "Sin ajuste de línea" : "Ajustar líneas"}
                </button>
            </div>
            <pre className={`qa-code${ajustar ? " is-wrapped" : ""}`} tabIndex={0} aria-label="Contenido de la evidencia">
                <code>
                    {lineas.map((l, i) => (
                        <span key={i} className="qa-code-line">
                            {l || " "}
                            {"\n"}
                        </span>
                    ))}
                </code>
            </pre>
        </div>
    );
}

/** La evidencia completa, según su tipo. */
export function Vista({ ev }: { ev: Evidencia }) {
    const contenido = useContenido(ev);
    if (!contenido) return <Cargando texto="Cargando evidencia…" />;
    if (contenido.error) return <Alerta tipo="danger">{contenido.error}</Alerta>;
    if (ev.tipo === "imagen") return <img className="qa-evidence-media" src={contenido.url} alt={ev.etiqueta ? `${ev.etiqueta}: ${ev.nombre}` : ev.nombre} />;
    if (ev.tipo === "video") return <video className="qa-evidence-media" src={contenido.url} controls autoPlay={false} preload="metadata" />;
    if (ev.tipo === "pdf") return <iframe className="qa-evidence-pdf" src={contenido.url} title={`PDF: ${ev.nombre}`} />;
    return <Codigo texto={contenido.texto ?? ""} json={ev.content_type.includes("json")} />;
}

async function descargar(ev: Evidencia, getToken: () => Promise<string | null>) {
    const res = await fetch(`/api/evidencias/${ev.id}/archivo`, { headers: { Authorization: `Bearer ${await getToken()}` } });
    const url = URL.createObjectURL(await res.blob());
    const a = Object.assign(document.createElement("a"), { href: url, download: ev.nombre });
    a.click();
    URL.revokeObjectURL(url);
}

/** Modal con la evidencia (Esc o el fondo lo cierran). */
export function ModalEvidencia({ ev, alCerrar }: { ev: Evidencia; alCerrar: () => void }) {
    const { getToken } = useAuth();
    const toast = useToast();
    const cerrar = useRef<HTMLButtonElement>(null);

    useEffect(() => {
        const previo = document.activeElement as HTMLElement | null;
        cerrar.current?.focus();
        document.body.classList.add("modal-open");
        const alTeclear = (e: KeyboardEvent) => e.key === "Escape" && alCerrar();
        document.addEventListener("keydown", alTeclear);
        return () => {
            document.body.classList.remove("modal-open");
            document.removeEventListener("keydown", alTeclear);
            previo?.focus();
        };
    }, [alCerrar]);

    const copiar = async () => {
        await navigator.clipboard.writeText(`${location.origin}${ev.url}`);
        toast("Enlace copiado. Para verlo hay que iniciar sesión en la plataforma.");
    };

    return (
        <>
            <div className="modal-backdrop fade show" />
            <div className="modal fade show d-block" role="dialog" aria-modal="true" aria-labelledby={`ev-${ev.id}-titulo`} onMouseDown={(e) => e.target === e.currentTarget && alCerrar()}>
                <div className="modal-dialog modal-xl modal-dialog-centered modal-dialog-scrollable">
                    <div className="modal-content admin-modal">
                        <div className="modal-header">
                            <div>
                                <h2 className="admin-h2 mb-0" id={`ev-${ev.id}-titulo`}>
                                    {ev.etiqueta ?? ev.nombre}
                                </h2>
                                <p className="admin-modal-lead admin-muted">
                                    {ev.nombre} · {tamano(ev.bytes)} · @{ev.subida_por} · {fecha(ev.subida_en)}
                                    {ev.datos_ocultos > 0 && ` · se ocultaron ${ev.datos_ocultos} dato(s) sensible(s)`}
                                </p>
                            </div>
                            <button ref={cerrar} type="button" className="btn-close" aria-label="Cerrar" onClick={alCerrar} />
                        </div>
                        <div className="modal-body">
                            <Vista ev={ev} />
                        </div>
                        <div className="modal-footer">
                            <button type="button" className="btn btn-sm btn-outline-secondary" onClick={() => void copiar()}>
                                <Icon name="link" /> Copiar enlace
                            </button>
                            <Link className="btn btn-sm btn-outline-secondary" href={ev.url}>
                                <Icon name="open_in_new" /> Abrir en una página
                            </Link>
                            <button type="button" className="btn btn-sm btn-primary" onClick={() => void descargar(ev, getToken)}>
                                <Icon name="download" /> Descargar
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </>
    );
}

function Fila({ ev, alAbrir, alBorrar }: { ev: Evidencia; alAbrir: () => void; alBorrar?: () => void }) {
    return (
        <li className="qa-evidence-row">
            <span className={`qa-evidence-icon is-${ev.tipo}`} aria-hidden="true">
                <Icon name={icono(ev)} />
            </span>
            <button type="button" className="qa-evidence-open" onClick={alAbrir}>
                <span className="cell-title">{ev.etiqueta ? `${ev.etiqueta} · ${ev.nombre}` : ev.nombre}</span>
                <span className="cell-sub">
                    {tamano(ev.bytes)} · @{ev.subida_por} · {fecha(ev.subida_en)}
                    {ev.datos_ocultos > 0 && (
                        <>
                            {" · "}
                            <Icon name="visibility_off" /> {ev.datos_ocultos} dato(s) oculto(s)
                        </>
                    )}
                </span>
            </button>
            <div className="row-actions">
                <button type="button" className="btn btn-sm btn-outline-secondary" onClick={alAbrir}>
                    <Icon name="visibility" /> Ver
                </button>
                {alBorrar && (
                    <button type="button" className="btn btn-sm btn-outline-danger btn-icon-only" title="Borrar" aria-label={`Borrar ${ev.nombre}`} onClick={alBorrar}>
                        <Icon name="delete" />
                    </button>
                )}
            </div>
        </li>
    );
}

type Subida = { nombre: string; estado: "subiendo" | "lista" | "error"; mensaje?: string };

export function Evidencias({ destino }: { destino: { resultado: number } | { bug: number } }) {
    const api = useApi();
    const { getToken } = useAuth();
    const yo = useYo();
    const toast = useToast();
    const consulta = "resultado" in destino ? `resultado=${destino.resultado}` : `bug=${destino.bug}`;
    const lista = useDatos<{ evidencias: Evidencia[] }>(`/api/evidencias?${consulta}`);
    const confirmar = useConfirmar();
    const [abierta, setAbierta] = useState<Evidencia | null>(null);
    const [subidas, setSubidas] = useState<Subida[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [texto, setTexto] = useState({ etiqueta: "Response" as (typeof ETIQUETAS_TEXTO)[number], valor: "" });
    const [arrastrando, setArrastrando] = useState(false);
    const entrada = useRef<HTMLInputElement>(null);
    const id = consulta.replace(/\W+/g, "-");
    const { recargar } = lista;

    const subir = useCallback(
        async (archivos: File[]) => {
            if (!archivos.length) return;
            setError(null);
            setSubidas(archivos.map((a) => ({ nombre: a.name, estado: "subiendo" })));
            let listas = 0;
            for (const [i, archivo] of archivos.entries()) {
                let estado: Subida;
                try {
                    const res = await fetch(`/api/evidencias?${consulta}`, {
                        method: "POST",
                        headers: { Authorization: `Bearer ${await getToken()}`, "Content-Type": tipoDe(archivo), "X-Nombre": encodeURIComponent(archivo.name) },
                        body: archivo,
                    });
                    const data = (await res.json().catch(() => ({}))) as Evidencia & { error?: string };
                    if (!res.ok) throw new ApiError(res.status, data.error ?? `Error ${res.status}`);
                    listas++;
                    estado = { nombre: archivo.name, estado: "lista", mensaje: data.datos_ocultos ? `se ocultaron ${data.datos_ocultos} dato(s) sensible(s)` : undefined };
                } catch (e) {
                    estado = { nombre: archivo.name, estado: "error", mensaje: (e as Error).message };
                }
                setSubidas((s) => s.map((x, j) => (j === i ? estado : x)));
            }
            if (listas) toast(listas === 1 ? "Evidencia agregada." : `${listas} evidencias agregadas.`);
            if (listas < archivos.length) toast(`${archivos.length - listas} archivo(s) no se pudieron subir.`, "error");
            recargar();
        },
        [consulta, getToken, recargar, toast],
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
            toast(r.datos_ocultos ? `Texto agregado. Se ocultaron ${r.datos_ocultos} dato(s) sensible(s).` : "Texto agregado como evidencia.");
            setTexto({ ...texto, valor: "" });
            setError(null);
            recargar();
        } catch (e) {
            setError((e as Error).message);
        }
    }

    async function borrar(ev: Evidencia) {
        const ok = await confirmar({ titulo: "Borrar evidencia", texto: `Se va a borrar ${ev.nombre}. Los enlaces a esta evidencia desde GitHub dejan de funcionar.`, confirmar: "Borrar", peligro: true });
        if (!ok) return;
        try {
            await api(`/api/evidencias/${ev.id}`, { method: "DELETE" });
            toast("Evidencia borrada.");
            recargar();
        } catch (e) {
            setError((e as Error).message);
        }
    }

    const evidencias = lista.datos?.evidencias ?? [];
    return (
        <div className="qa-stack">
            {!lista.datos && <Cargando />}
            {lista.datos &&
                (evidencias.length ? (
                    <ul className="qa-evidence-list" aria-label="Evidencias">
                        {evidencias.map((e) => (
                            <Fila key={e.id} ev={e} alAbrir={() => setAbierta(e)} alBorrar={yo.permisos.administrar ? () => void borrar(e) : undefined} />
                        ))}
                    </ul>
                ) : (
                    <p className="admin-muted mb-0">Todavía no hay evidencias.</p>
                ))}

            {yo.permisos.escribir && (
                <>
                    <div
                        className={`qa-dropzone${arrastrando ? " is-over" : ""}`}
                        onDragOver={(e) => {
                            e.preventDefault();
                            setArrastrando(true);
                        }}
                        onDragLeave={() => setArrastrando(false)}
                        onDrop={alSoltar}
                        onPaste={alPegar}
                        tabIndex={0}
                        aria-label="Zona para subir evidencias: soltá archivos o pegá una captura con Ctrl+V"
                    >
                        <Icon name="upload_file" />
                        <strong>Agregar evidencias</strong>
                        <span>
                            Soltá archivos acá o pegá una captura con <kbd>Ctrl</kbd> + <kbd>V</kbd>. <strong>Se suben solos</strong>: no hace falta tocar otro botón.
                        </span>
                        <button type="button" className="btn btn-sm btn-outline-primary" onClick={() => entrada.current?.click()}>
                            <Icon name="folder_open" /> Elegir archivos
                        </button>
                        <span className="admin-muted small">
                            Imágenes y GIF (hasta 10 MB) · video MP4 o WebM (hasta 50 MB) · PDF (hasta 10 MB) · JSON, HAR, log o texto (hasta 2 MB)
                        </span>
                        <input
                            ref={entrada}
                            type="file"
                            hidden
                            multiple
                            accept="image/png,image/jpeg,image/webp,image/gif,video/mp4,video/webm,application/pdf,.pdf,.json,.har,.log,.txt"
                            onChange={(e) => {
                                void subir([...(e.target.files ?? [])]);
                                e.target.value = "";
                            }}
                        />
                    </div>

                    {subidas.length > 0 && (
                        <ul className="qa-uploads" aria-live="polite" aria-label="Archivos subidos">
                            {subidas.map((s, i) => (
                                <li key={`${s.nombre}-${i}`} className={`is-${s.estado}`}>
                                    {s.estado === "subiendo" ? <span className="spinner-border spinner-border-sm" aria-hidden="true" /> : <Icon name={s.estado === "lista" ? "check_circle" : "error"} />}
                                    <span>
                                        <strong>{s.nombre}</strong> · {s.estado === "subiendo" ? "subiendo…" : s.estado === "lista" ? "agregada" : "no se pudo subir"}
                                        {s.mensaje && ` (${s.mensaje})`}
                                    </span>
                                </li>
                            ))}
                        </ul>
                    )}

                    <form className="qa-paste-form" onSubmit={guardarTexto}>
                        <div className="qa-paste">
                            <Selector id={`${id}-etiqueta`} etiqueta="Qué es" valor={texto.etiqueta} opciones={ETIQUETAS_TEXTO.map((e) => [e, e] as const)} onChange={(etiqueta) => setTexto({ ...texto, etiqueta })} />
                            <div className="form-group">
                                <div className="form-label-container">
                                    <label className="form-label" htmlFor={`${id}-texto`}>
                                        O pegá texto: request, response, curl o consola
                                    </label>
                                </div>
                                <textarea className="form-control qa-mono" id={`${id}-texto`} rows={4} value={texto.valor} onChange={(e) => setTexto({ ...texto, valor: e.target.value })} />
                            </div>
                        </div>
                        <div className="qa-actions">
                            <button type="submit" className="btn btn-sm btn-primary" disabled={!texto.valor.trim()}>
                                <Icon name="content_paste_go" /> Agregar texto
                            </button>
                            <span className="admin-muted small">Los tokens, cookies, claves y firmas de URL se ocultan antes de guardar.</span>
                        </div>
                    </form>

                    <p className="admin-note mb-0">
                        <Icon name="shield_person" /> Usá solo los datos de prueba de la guía (sección 3). Las capturas, los videos y los PDF no se revisan solos: tapá cualquier dato
                        personal real antes de subirlos.
                    </p>
                </>
            )}
            {error && <Alerta tipo="danger">{error}</Alerta>}
            {abierta && <ModalEvidencia ev={abierta} alCerrar={() => setAbierta(null)} />}
        </div>
    );
}
