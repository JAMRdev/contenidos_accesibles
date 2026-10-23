// Entorno de capturas para revisar el diseño sin login ni Worker (npm run capturas). Responde la API
// con datos de ejemplo y abre la ruta pedida con ?ruta=/bugs/1. No forma parte del build.
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import catalogo from "../../worker/catalogo.gen.json";
import { App } from "../App";
import "../styles.css";

const ahora = "2026-10-22T19:43:00.000Z";
const casos = catalogo.casos.map((c) => ({ ...c, area: "backend", manual: false, activo: true, creado_por: null }));
const caso = (id: string) => casos.find((c) => c.id === id)!;

const yo = { github: "jamrdev", nombre: "José Monsalve", avatar_url: null, rol: "admin", permisos: { escribir: true, administrar: true } };

const sesion = { id: 3, titulo: "Ronda 22/10", entorno: "produccion", area: "backend", url: "https://innovalab-educacion-e18-test.up.railway.app/api/v1", version: "0.1.0", notas: "Probando process-text y uploads.", estado: "abierta", creada_por: "jamrdev", creada_en: ahora, cerrada_por: null, cerrada_en: null, execution_issue: null };
const avance = { total: 140, pendiente: 134, paso: 3, fallo: 2, bloqueado: 1, omitido: 0 };

const evidencias = [
    { id: 1, resultado_id: null, bug_id: 1, tipo: "video", etiqueta: null, nombre: "scanner.mp4", content_type: "video/mp4", bytes: 6081740, datos_ocultos: 0, subida_por: "jamrdev", subida_en: ahora, url: "/evidencias/1" },
    { id: 2, resultado_id: null, bug_id: 1, tipo: "texto", etiqueta: "Consola", nombre: "fotosintesis_material_con_barreras-adaptado.txt", content_type: "text/plain", bytes: 5530, datos_ocultos: 2, subida_por: "jamrdev", subida_en: ahora, url: "/evidencias/2" },
    { id: 3, resultado_id: null, bug_id: 1, tipo: "texto", etiqueta: "Response", nombre: "response.json", content_type: "application/json", bytes: 2970, datos_ocultos: 0, subida_por: "jamrdev", subida_en: ahora, url: "/evidencias/3" },
    { id: 4, resultado_id: null, bug_id: 1, tipo: "imagen", etiqueta: "Pantalla", nombre: "captura-error.png", content_type: "image/png", bytes: 182300, datos_ocultos: 0, subida_por: "jamrdev", subida_en: ahora, url: "/evidencias/4" },
    { id: 5, resultado_id: null, bug_id: 1, tipo: "pdf", etiqueta: null, nombre: "informe_accesibilidad.pdf", content_type: "application/pdf", bytes: 48211, datos_ocultos: 0, subida_por: "jamrdev", subida_en: ahora, url: "/evidencias/5" },
];

const bug = {
    id: 1,
    caso: { id: "QA-4.1-01", seccion: "4.1", titulo: caso("QA-4.1-01").titulo },
    sesion_id: 3,
    entorno: "produccion",
    area: "backend",
    titulo: "La raíz responde 500 en producción",
    descripcion: "Pasa solo la primera vez después de que el servicio despierta.",
    severidad: "baja",
    reporte: { pasos: "Llamar a GET /\nRepetir la llamada", esperado: caso("QA-4.1-01").esperado, obtenido: '500\n{"detail": "Internal Server Error"}', url: sesion.url, version: "0.1.0" },
    estado: "abierto",
    issue: { numero: 2, url: "https://github.com/e18-innovalab/contenidos_accesibles/issues/2", estado: "open", motivo: null, tablero: null },
    sincronizado_en: ahora,
    creado_por: "jamrdev",
    creado_en: ahora,
    actualizado_en: ahora,
    historial: [
        { de: null, a: "borrador", origen: "qa", actor: "jamrdev", nota: null, en: "2026-10-22T18:07:00.000Z" },
        { de: "borrador", a: "abierto", origen: "qa", actor: "jamrdev", nota: "Publicado en GitHub como #2", en: ahora },
    ],
    comentarios: [
        { id: 1, autor: "jamrdev", texto: "Pasa también con PDF.", origen: "plataforma", en_github: true, publicado_en_github: true, creado_en: ahora },
        { id: 2, autor: "malessan4", texto: "Lo miro mañana.", origen: "github", en_github: false, publicado_en_github: false, creado_en: ahora },
    ],
};

const bugBorrador: Record<string, unknown> & { reporte: Record<string, unknown>; historial: unknown[] } = {
    ...bug,
    id: 2,
    estado: "borrador",
    issue: null,
    titulo: "process-text cuenta mal las palabras",
    caso: { id: "QA-4.3-01", seccion: "4.3", titulo: caso("QA-4.3-01").titulo },
    reporte: { ...bug.reporte, pasos: "Enviar {\"text\": \"Leé el texto y respondé las preguntas.\"}\nMirar word_count", esperado: caso("QA-4.3-01").esperado, obtenido: "" },
    descripcion: "",
    historial: [bug.historial[0]],
    comentarios: [],
};

// Resultados de la sesión #3 (mutables: la captura de "registrar un resultado" los cambia)
const resultados = new Map(
    casos.slice(0, 6).map((c, i) => [c.id, { id: i + 1, estado: ["paso", "fallo", "paso", "bloqueado", "paso", "fallo"][i], notas: i === 1 ? "Respondió 500" : null, actualizado_por: "jamrdev", actualizado_en: ahora }]),
);

const casosManuales = [
    { id: "QA-FE-001", area: "frontend", seccion: "Login de docentes", titulo: "Entrar con Google", endpoint: "/login", precondiciones: [], pasos: ["Abrir /login", "Elegir Google"], datos: null, esperado: "Entra y ve el inicio.", severidad: "alta", etiquetas: ["login"], mock: false, ia_real: false, destructivo: false, manual: true, activo: true, creado_por: "camposmatias" },
    { id: "QA-FE-002", area: "frontend", seccion: "Subir material", titulo: "Arrastrar un PDF", endpoint: "/materiales/nuevo", precondiciones: ["Sesión iniciada"], pasos: ["Arrastrar un PDF de 2 MB"], datos: "PDF de texto", esperado: "Se ve la barra de progreso y el diagnóstico.", severidad: "media", etiquetas: ["archivos"], mock: true, ia_real: false, destructivo: false, manual: true, activo: true, creado_por: "camposmatias" },
];

const fijos: [RegExp, (m: RegExpMatchArray, url: URL) => unknown][] = [
    [/^\/health$/, () => ({ ok: true, servicio: "innovalab-qa", version: "2d7dbd50", d1: "ok", r2: "ok", ultima_cron: ahora, casos: 150 })],
    [/^\/api\/me$/, () => yo],
    [/^\/api\/bugs\/resumen$/, () => ({ por_estado: { borrador: 1, abierto: 1, en_curso: 0, resuelto: 1, verificado: 0, reabierto: 0, descartado: 0 } })],
    [/^\/api\/bugs\/(\d+)\/vista-previa$/, (m) => {
        const b = m[1] === "2" ? bugBorrador : bug;
        return {
            titulo: `[QA - Issue] ${(b.caso as { id: string }).id} · ${b.titulo}`,
            cuerpo: "<!-- qa-case: QA-4.3-01 -->\n> [!IMPORTANT]\n> **Reporte completo en la plataforma de QA:** …",
            etiquetas: ["[QA - Issue]", "bug", "testing", "backend"],
            repo: "e18-innovalab/contenidos_accesibles",
            faltan: m[1] === "2" && !bugBorrador.reporte.obtenido ? ["resultado obtenido"] : [],
        };
    }],
    [/^\/api\/bugs\/(\d+)\/duplicados$/, () => ({ plataforma: [], github: { disponible: true, issues: [], error: null } })],
    [/^\/api\/bugs\/(\d+)$/, (m) => (m[1] === "2" ? bugBorrador : bug)],
    [/^\/api\/bugs$/, () => ({ bugs: [bug, bugBorrador], siguiente: null })],
    [/^\/api\/evidencias$/, () => ({ evidencias })],
    [/^\/api\/evidencias\/(\d+)$/, (m) => evidencias.find((e) => e.id === Number(m[1]))],
    [/^\/api\/casos\/secciones$/, (_m, url) =>
        url.searchParams.get("area") === "frontend"
            ? { secciones: [{ seccion: "Login de docentes", titulo: "Login de docentes", casos: 1 }, { seccion: "Subir material", titulo: "Subir material", casos: 1 }] }
            : { secciones: catalogo.secciones }],
    [/^\/api\/casos\/(QA-[^/]+)$/, (m) => ({ caso: casosManuales.find((c) => c.id === m[1]) ?? caso(m[1]), historial: [{ resultado_id: 1, estado: "fallo", notas: "Respondió 500", actualizado_por: "jamrdev", actualizado_en: ahora, sesion: { id: 3, titulo: "Ronda 22/10", entorno: "produccion", version: "0.1.0" } }], bugs: [{ id: 1, titulo: bug.titulo, estado: "abierto", severidad: "baja", issue: bug.issue, creado_en: ahora }] })],
    [/^\/api\/casos$/, (_m, url) => {
        const area = url.searchParams.get("area");
        const lista = area === "frontend" ? casosManuales : casos.slice(0, 30);
        return { secciones: catalogo.secciones, total: lista.length, casos: lista };
    }],
    [/^\/api\/sesiones\/(\d+)\/publicacion$/, () => ({ bugs: [{ id: 2, titulo: bugBorrador.titulo, caso: "QA-4.3-01", listo: false, faltan: ["resultado obtenido"] }], propuestas_cierre: [], ejecucion: { accion: "crear", numero: null }, comentarios: 0, repo: "e18-innovalab/contenidos_accesibles", github_disponible: true })],
    [/^\/api\/sesiones\/(\d+)$/, () => ({
        sesion,
        avance,
        casos: casos.slice(0, 12).map((c, i) => ({
            caso: c,
            aplica: !c.destructivo,
            resultado: resultados.get(c.id) ?? null,
        })),
    })],
    [/^\/api\/sesiones$/, () => ({ sesiones: [{ ...sesion, avance }, { ...sesion, id: 2, titulo: "Login del front", area: "frontend", entorno: "staging", estado: "cerrada", avance: { total: 2, pendiente: 0, paso: 1, fallo: 1, bloqueado: 0, omitido: 0 } }], siguiente: null })],
    [/^\/api\/resultados\/(\d+)\/comentarios$/, () => ({ comentarios: [] })],
    [/^\/api\/miembros$/, () => ({ fijos: [{ github: "JAMRdev", rol: "admin" }, { github: "malessan4", rol: "admin" }], miembros: [{ github: "camposmatias", rol: "qa", habilitado: true, agregado_por: "JAMRdev" }, { github: "Muselt", rol: "viewer", habilitado: false, agregado_por: "JAMRdev" }] })],
];

const PNG = "data:image/svg+xml;utf8," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="800" height="450"><rect width="100%" height="100%" fill="#e3f1f6"/><text x="50%" y="50%" font-size="32" text-anchor="middle" fill="#08202f">Captura de ejemplo</text></svg>');

const fetchReal = window.fetch.bind(window);
window.fetch = async (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input), location.origin);
    if (url.origin !== location.origin || !(url.pathname.startsWith("/api/") || url.pathname === "/health")) return fetchReal(input, init);
    const archivo = url.pathname.match(/^\/api\/evidencias\/(\d+)\/archivo$/);
    if (archivo) {
        const ev = evidencias.find((e) => e.id === Number(archivo[1]))!;
        if (ev.tipo === "imagen") return fetchReal(PNG);
        if (ev.content_type === "application/json") return new Response(JSON.stringify({ detail: "Internal Server Error", analysis_id: "1b2c", barriers: [{ id: "bar-1", category: "comprension" }] }), { headers: { "Content-Type": "application/json" } });
        if (ev.tipo === "texto") return new Response("GET /api/v1/files\nAuthorization: [OCULTO]\n200 OK\n", { headers: { "Content-Type": "text/plain" } });
        return new Response(new Blob([]), { headers: { "Content-Type": ev.content_type } });
    }
    const metodo = init?.method ?? "GET";
    if (metodo !== "GET") {
        const datos = init?.body && typeof init.body === "string" ? (JSON.parse(init.body) as Record<string, unknown>) : {};
        const resultado = url.pathname.match(/^\/api\/sesiones\/3\/resultados\/(.+)$/);
        if (resultado && metodo === "PUT") {
            const r = { id: resultados.size + 1, estado: datos.estado as string, notas: (datos.notas as string) ?? null, actualizado_por: "jamrdev", actualizado_en: new Date().toISOString() };
            resultados.set(decodeURIComponent(resultado[1]), r);
            return Response.json(r);
        }
        if (url.pathname === "/api/bugs/2" && metodo === "PATCH") {
            const { estado, titulo, descripcion, severidad, ...reporte } = datos;
            Object.assign(bugBorrador, Object.fromEntries(Object.entries({ estado, titulo, descripcion, severidad }).filter(([, v]) => v !== undefined)));
            Object.assign(bugBorrador.reporte, reporte);
            bugBorrador.actualizado_en = new Date().toISOString();
            return Response.json(bugBorrador);
        }
        if (url.pathname === "/api/bugs/2/publicar") {
            const issue = { numero: 136, url: "https://github.com/e18-innovalab/contenidos_accesibles/issues/136", estado: "open", motivo: null, tablero: "Backlog" };
            Object.assign(bugBorrador, { estado: "abierto", issue, actualizado_en: new Date().toISOString() });
            bugBorrador.historial.push({ de: "borrador", a: "abierto", origen: "qa", actor: "jamrdev", nota: "Publicado en GitHub como #136", en: new Date().toISOString() });
            return Response.json({ accion: "creado", issue, comentarios: 0, avisos: [], bug: bugBorrador });
        }
        if (/^\/api\/bugs\/\d+\/sincronizar$/.test(url.pathname)) return Response.json({ bugs: 1, cambios: [], comentarios: 0, errores: [] });
        if (url.pathname === "/api/evidencias/texto") {
            const ev = { id: evidencias.length + 1, resultado_id: null, bug_id: 2, tipo: "texto", etiqueta: String(datos.etiqueta) as never, nombre: "response.json", content_type: "application/json", bytes: 120, datos_ocultos: 1, subida_por: "jamrdev", subida_en: new Date().toISOString(), url: `/evidencias/${evidencias.length + 1}` };
            evidencias.push(ev);
            return Response.json(ev);
        }
        return Response.json({ error: "Las capturas no guardan este cambio." }, { status: 409 });
    }
    for (const [patron, respuesta] of fijos) {
        const m = url.pathname.match(patron);
        if (m) return Response.json(respuesta(m, url));
    }
    return Response.json({ error: `Sin datos de ejemplo para ${url.pathname}` }, { status: 404 });
};

const parametros = new URLSearchParams(location.search);
const ruta = parametros.get("ruta") ?? "/";
// ?clic=<selector> hace clic en ese elemento al cargar (por ejemplo, para abrir un modal)
const clic = parametros.get("clic");
history.replaceState(null, "", ruta);
if (clic) setTimeout(() => (document.querySelector(clic) as HTMLElement | null)?.click(), 1500);

createRoot(document.getElementById("root")!).render(
    <StrictMode>
        <App />
    </StrictMode>,
);
