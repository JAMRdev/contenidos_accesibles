// Validación del catálogo de casos (tester/casos/*.yml, Issue #125). La usa scripts/catalogo.ts.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { parseDocument } from "yaml";
import { archivoSchema, ID_CASO, ID_CASO_EN_TEXTO, type Caso, type Catalogo, type Seccion } from "../src/catalogo/esquema.ts";

export type Opciones = {
    // Carpeta con los .yml
    casos: string;
    // Texto de la guía de QA (tiene que mencionar todos los IDs del catálogo y ninguno más)
    guia: string;
    // Contenido de ids-retirados.txt ("" si no existe)
    retirados: string;
    // IDs de una versión anterior del catálogo (la rama base del PR); null para no comparar
    idsAnteriores: string[] | null;
    // Cómo se muestran las rutas en los mensajes
    nombre?: (archivo: string) => string;
};

export type Resultado = { errores: string[]; catalogo: Catalogo };

export function leerYaml(texto: string, origen: string, errores: string[]): unknown {
    const doc = parseDocument(texto, { uniqueKeys: true });
    for (const e of doc.errors) errores.push(`${origen}: ${e.message}`);
    return doc.errors.length ? null : doc.toJS();
}

export function idsDelArchivo(datos: unknown): string[] {
    const casos = (datos as { casos?: { id?: unknown }[] } | null)?.casos;
    return Array.isArray(casos) ? casos.map((c) => String(c?.id)) : [];
}

export function validarCatalogo(op: Opciones): Resultado {
    const errores: string[] = [];
    const nombre = op.nombre ?? ((archivo: string) => archivo);
    const secciones: Seccion[] = [];
    const casos: Caso[] = [];
    const vistos = new Map<string, string>();

    const archivos = readdirSync(op.casos)
        .filter((f) => f.endsWith(".yml"))
        .sort();
    if (!archivos.length) errores.push(`${nombre(op.casos)}: no hay archivos .yml.`);

    for (const archivo of archivos) {
        const ruta = nombre(join(op.casos, archivo));
        const datos = leerYaml(readFileSync(join(op.casos, archivo), "utf8"), ruta, errores);
        if (datos === null) continue;
        const resultado = archivoSchema.safeParse(datos);
        if (!resultado.success) {
            const lista = (datos as { casos?: { id?: unknown }[] }).casos;
            for (const issue of resultado.error.issues) {
                // casos.6.esperado → QA-4.10-07.esperado (más fácil de encontrar en el archivo)
                const [raiz, indice, ...resto] = issue.path.map(String);
                const id = raiz === "casos" && Array.isArray(lista) ? lista[Number(indice)]?.id : undefined;
                const donde = id ? [id, ...resto].join(".") : issue.path.join(".") || "(raíz)";
                errores.push(`${ruta}: ${donde}: ${issue.message}`);
            }
            continue;
        }
        const { seccion, titulo } = resultado.data;
        for (const caso of resultado.data.casos) {
            if (ID_CASO.exec(caso.id)?.[1] !== seccion) {
                errores.push(`${ruta}: ${caso.id} no corresponde a la sección ${seccion} (QA-${seccion}-NN).`);
            }
            const previo = vistos.get(caso.id);
            if (previo) errores.push(`${ruta}: ${caso.id} repetido (ya está en ${previo}).`);
            vistos.set(caso.id, ruta);
            casos.push({ ...caso, seccion });
        }
        secciones.push({ seccion, titulo, archivo, casos: resultado.data.casos.length });
    }

    const retirados = new Set(
        op.retirados
            .split("\n")
            .map((l) => l.replace(/#.*/, "").trim())
            .filter(Boolean),
    );
    for (const id of retirados) {
        if (!ID_CASO.test(id)) errores.push(`ids-retirados.txt: "${id}" no es un ID de caso.`);
        if (vistos.has(id)) errores.push(`${id} está retirado (ids-retirados.txt): usá un ID nuevo.`);
    }

    for (const id of op.idsAnteriores ?? []) {
        if (!vistos.has(id) && !retirados.has(id)) {
            errores.push(`${id} se borró del catálogo: agregalo a tester/casos/ids-retirados.txt para que no se reutilice.`);
        }
    }

    // La guía muestra los IDs: el texto de cada caso vive en un solo lugar (el catálogo)
    const enGuia = new Set(op.guia.match(ID_CASO_EN_TEXTO) ?? []);
    for (const id of vistos.keys()) {
        if (!enGuia.has(id)) errores.push(`${id} no aparece en la guía.`);
    }
    for (const id of enGuia) {
        if (!vistos.has(id)) errores.push(`La guía menciona ${id}, que no está en el catálogo.`);
    }

    return { errores, catalogo: { secciones, casos } };
}
