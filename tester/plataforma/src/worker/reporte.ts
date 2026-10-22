// Issue de GitHub de un bug (Issue #129): título, cuerpo en Markdown y etiquetas. Es lo que se
// muestra en la vista previa y lo que publica #130, así que hay una sola plantilla.
import type { Caso } from "../catalogo/esquema.ts";

export const ETIQUETA_QA = "[QA - Issue]";
export const ETIQUETAS_BUG = [ETIQUETA_QA, "bug", "testing", "backend"];

const ENTORNO = { produccion: "Producción", staging: "Staging", local: "Local" } as const;
const SEVERIDAD = { alta: "Alta", media: "Media", baja: "Baja" } as const;

export type DatosReporte = {
    id: number;
    titulo: string;
    severidad: keyof typeof SEVERIDAD;
    entorno: keyof typeof ENTORNO | null;
    url: string | null;
    version: string | null;
    pasos: string;
    esperado: string;
    obtenido: string;
    notas: string;
    sesion_id: number | null;
    creado_por: string;
};

export type EvidenciaReporte = { id: number; etiqueta: string | null; nombre: string; tipo: string };

/** Marca oculta que identifica el caso en el issue (para no duplicar bugs del mismo caso). */
export function marcaCaso(casoId: string): string {
    return `<!-- qa-case: ${casoId} -->`;
}

/** Marca oculta del bug de la plataforma: permite retomar una publicación que quedó a medias. */
export function marcaBug(bugId: number): string {
    return `<!-- qa-bug: ${bugId} -->`;
}

/** Marca oculta del issue de ejecución de una sesión. */
export function marcaSesion(sesionId: number): string {
    return `<!-- qa-session: ${sesionId} -->`;
}

/** Qué le falta al reporte para poder publicarse. */
export function faltantes(r: Pick<DatosReporte, "titulo" | "pasos" | "obtenido" | "esperado">): string[] {
    const faltan: string[] = [];
    if (!r.titulo.trim()) faltan.push("título");
    if (!r.pasos.trim()) faltan.push("pasos para reproducir");
    if (!r.esperado.trim()) faltan.push("resultado esperado");
    if (!r.obtenido.trim()) faltan.push("resultado obtenido");
    return faltan;
}

/** Lista numerada a partir de líneas (si ya viene numerada o con viñetas, se respeta). */
function pasosMarkdown(pasos: string): string {
    const lineas = pasos.split("\n").map((l) => l.trim()).filter(Boolean);
    if (lineas.some((l) => /^(\d+[.)]|[-*])\s/.test(l))) return lineas.join("\n");
    return lineas.map((l, i) => `${i + 1}. ${l}`).join("\n");
}

function bloque(texto: string): string {
    // Si el texto ya trae ``` usa una cerca más larga para no romper el bloque
    const cerca = texto.includes("```") ? "````" : "```";
    return `${cerca}\n${texto.trim()}\n${cerca}`;
}

const TIPO_EVIDENCIA: Record<string, string> = { imagen: "🖼️ imagen", video: "🎬 video", texto: "📄 texto", pdf: "📕 PDF" };

function cantidad(n: number, singular: string, plural: string): string {
    return `${n} ${n === 1 ? singular : plural}`;
}

/** Aviso destacado al principio del issue: el reporte completo está en la plataforma. */
function avisoPlataforma(r: DatosReporte, evidencias: EvidenciaReporte[], origenWeb: string): string {
    const adjuntos =
        evidencias.length === 0 ? "el detalle" : evidencias.length === 1 ? "la evidencia adjunta" : `las ${evidencias.length} evidencias adjuntas (capturas, videos y archivos)`;
    return [
        "> [!IMPORTANT]",
        `> **Reporte completo en la plataforma de QA:** [bug #${r.id}](${origenWeb}/bugs/${r.id}). Ahí se ven ${adjuntos}, los comentarios y el historial de estados.`,
        "> Pide iniciar sesión con GitHub (la misma cuenta del panel).",
    ].join("\n");
}

function seccionEvidencias(evidencias: EvidenciaReporte[], origenWeb: string): string[] {
    if (!evidencias.length) return ["### Evidencias", "_Este bug no tiene evidencias adjuntas en la plataforma de QA._"];
    return [
        "### Evidencias",
        `**${cantidad(evidencias.length, "evidencia adjunta", "evidencias adjuntas")}** en la plataforma de QA (piden iniciar sesión):`,
        "",
        ...evidencias.map((e) => `- ${TIPO_EVIDENCIA[e.tipo] ?? e.tipo}: [${e.etiqueta ? `${e.etiqueta} · ${e.nombre}` : e.nombre}](${origenWeb}/evidencias/${e.id})`),
    ];
}

export function armarIssue(r: DatosReporte, caso: Caso, evidencias: EvidenciaReporte[], origenWeb: string) {
    const titulo = `${ETIQUETA_QA} ${caso.id} · ${r.titulo.trim()}`;
    const entorno = [r.entorno ? ENTORNO[r.entorno] : null, r.url ? `\`${r.url}\`` : null, r.version ? `backend v${r.version}` : null]
        .filter(Boolean)
        .join(" · ");
    const partes = [
        `${marcaCaso(caso.id)}${marcaBug(r.id)}`,
        avisoPlataforma(r, evidencias, origenWeb),
        "",
        `**Caso:** \`${caso.id}\` · ${caso.titulo}${caso.endpoint ? ` (\`${caso.endpoint}\`)` : ""}`,
        `**Entorno:** ${entorno || "sin datos"}`,
        `**Severidad:** ${SEVERIDAD[r.severidad]}`,
        "",
        "### Pasos para reproducir",
        pasosMarkdown(r.pasos) || "_Sin pasos._",
        "",
        "### Resultado esperado",
        r.esperado.trim() || "_Sin completar._",
        "",
        "### Resultado obtenido",
        r.obtenido.includes("\n") ? bloque(r.obtenido) : r.obtenido.trim() || "_Sin completar._",
    ];
    if (r.notas.trim()) partes.push("", "### Notas", r.notas.trim());
    partes.push("", ...seccionEvidencias(evidencias, origenWeb));
    partes.push(
        "",
        "---",
        `Reportado por @${r.creado_por} desde la [plataforma de QA](${origenWeb}/bugs/${r.id})${r.sesion_id ? ` (sesión #${r.sesion_id})` : ""}.`,
    );
    return { titulo, cuerpo: partes.join("\n"), etiquetas: ETIQUETAS_BUG };
}

/** Comentario en un issue abierto del mismo caso cuando el bug vuelve a aparecer. */
export function comentarioReincidencia(r: DatosReporte, caso: Caso, evidencias: EvidenciaReporte[], origenWeb: string): string {
    // El mismo reporte, sin las marcas ocultas (el issue ya tiene la del caso)
    const cuerpo = armarIssue(r, caso, evidencias, origenWeb).cuerpo.split("\n").slice(1).join("\n");
    return `**Se volvió a reproducir este bug** en la plataforma de QA (bug #${r.id}).\n\n${cuerpo}`;
}

export function comentarioPropuestaCierre(caso: Caso, sesion: { id: number; titulo: string; version: string | null }, origenWeb: string): string {
    return [
        `**El caso \`${caso.id}\` pasó** en la sesión de QA [#${sesion.id} · ${sesion.titulo}](${origenWeb}/sesiones/${sesion.id})${sesion.version ? ` (backend v${sesion.version})` : ""}.`,
        "",
        "Propongo cerrar este issue si el arreglo ya está en esa versión. La plataforma de QA no cierra issues sola.",
    ].join("\n");
}

export function comentarioInterno(autor: string, texto: string, bugId: number, origenWeb: string): string {
    const cita = texto.trim().split("\n").map((l) => `> ${l}`).join("\n");
    return `**@${autor}** comentó desde la [plataforma de QA](${origenWeb}/bugs/${bugId}):\n\n${cita}`;
}

const RESULTADO = { paso: "✅ Pasó", fallo: "❌ Falló", bloqueado: "⛔ Bloqueado", omitido: "⏭️ Omitido" } as const;

export type FilaEjecucion = {
    caso: Caso;
    estado: keyof typeof RESULTADO;
    bug: { numero: number; url: string } | null;
    evidencias: number[];
};

export type SesionEjecucion = {
    id: number;
    titulo: string;
    entorno: keyof typeof ENTORNO;
    url: string;
    version: string | null;
    creada_por: string;
    creada_en: string;
    estado: "abierta" | "cerrada";
};

/** Issue de ejecución de una sesión: resumen y tabla de casos con su resultado, bug y evidencias. */
export function armarEjecucion(s: SesionEjecucion, filas: FilaEjecucion[], total: number, origenWeb: string) {
    const fecha = s.creada_en.slice(0, 10);
    const titulo = `${ETIQUETA_QA} Ejecución ${fecha} · ${ENTORNO[s.entorno]} · ${s.titulo}`;
    const cuenta = (e: keyof typeof RESULTADO) => filas.filter((f) => f.estado === e).length;
    const partes = [
        marcaSesion(s.id),
        "> [!IMPORTANT]",
        `> **Detalle completo en la plataforma de QA:** [sesión #${s.id}](${origenWeb}/sesiones/${s.id}). Ahí están las notas de cada caso, las evidencias y los comentarios.`,
        "> Pide iniciar sesión con GitHub (la misma cuenta del panel).",
        "",
        `**Sesión:** [#${s.id} · ${s.titulo}](${origenWeb}/sesiones/${s.id})${s.estado === "abierta" ? " (todavía abierta)" : ""}`,
        `**Entorno:** ${ENTORNO[s.entorno]} · \`${s.url}\`${s.version ? ` · backend v${s.version}` : ""}`,
        `**QA:** @${s.creada_por} · ${fecha}`,
        "",
        "| Pasó | Falló | Bloqueado | Omitido | Pendientes |",
        "| :---: | :---: | :---: | :---: | :---: |",
        `| ${cuenta("paso")} | ${cuenta("fallo")} | ${cuenta("bloqueado")} | ${cuenta("omitido")} | ${Math.max(total - filas.length, 0)} |`,
        "",
        "### Casos",
    ];
    if (filas.length) {
        partes.push("| Caso | Título | Resultado | Bug | Evidencias |", "| :--- | :--- | :--- | :--- | :--- |");
        for (const f of filas) {
            const titulo = f.caso.titulo.replace(/\|/g, "\\|");
            const bug = f.bug ? `#${f.bug.numero}` : "—";
            const evidencias = f.evidencias.length ? f.evidencias.map((id, i) => `[${i + 1}](${origenWeb}/evidencias/${id})`).join(" ") : "—";
            partes.push(`| \`${f.caso.id}\` | ${titulo} | ${RESULTADO[f.estado]} | ${bug} | ${evidencias} |`);
        }
    } else {
        partes.push("_Todavía no hay resultados registrados._");
    }
    partes.push("", "---", "_Generado por la plataforma de QA; publicar de nuevo actualiza este issue. Las evidencias piden iniciar sesión._");
    return { titulo, cuerpo: partes.join("\n"), etiquetas: [ETIQUETA_QA, "testing"] };
}
