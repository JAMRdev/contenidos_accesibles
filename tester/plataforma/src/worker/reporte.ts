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

export function armarIssue(r: DatosReporte, caso: Caso, evidencias: EvidenciaReporte[], origenWeb: string) {
    const titulo = `${ETIQUETA_QA} ${caso.id} · ${r.titulo.trim()}`;
    const entorno = [r.entorno ? ENTORNO[r.entorno] : null, r.url ? `\`${r.url}\`` : null, r.version ? `backend v${r.version}` : null]
        .filter(Boolean)
        .join(" · ");
    const partes = [
        marcaCaso(caso.id),
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
    if (evidencias.length) {
        partes.push(
            "",
            "### Evidencias",
            ...evidencias.map((e) => `- [${e.etiqueta ?? e.nombre}](${origenWeb}/evidencias/${e.id}) (${e.tipo}${e.etiqueta ? `, ${e.nombre}` : ""})`),
            "",
            "_Las evidencias están en la plataforma de QA y piden iniciar sesión._",
        );
    }
    partes.push(
        "",
        "---",
        `Reportado por @${r.creado_por} desde la [plataforma de QA](${origenWeb}/bugs/${r.id})${r.sesion_id ? ` (sesión #${r.sesion_id})` : ""}.`,
    );
    return { titulo, cuerpo: partes.join("\n"), etiquetas: ETIQUETAS_BUG };
}
