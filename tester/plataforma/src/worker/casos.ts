// Todos los casos de prueba: los del repo (catálogo empaquetado, área backend) y los manuales
// creados desde la plataforma (tabla qa_cases, cualquier área). Las rutas usan esto en lugar del
// catálogo directo, así el frontend (que no tiene casos en el repo) funciona igual.
import type { Caso } from "../catalogo/esquema.ts";
import { catalogo } from "./catalogo";
import type { Env } from "./env";

export const AREAS = ["backend", "frontend"] as const;
export type Area = (typeof AREAS)[number];

const PREFIJO: Record<Area, string> = { backend: "BE", frontend: "FE" };

export type CasoQA = Caso & { area: Area; manual: boolean; activo: boolean; creado_por: string | null };

export type FilaCaso = {
    id: string;
    area: Area;
    section: string;
    title: string;
    location: string | null;
    preconditions: string;
    steps: string;
    test_data: string | null;
    expected: string;
    severity: Caso["severidad"];
    tags: string;
    mock: number;
    ia_real: number;
    destructive: number;
    active: number;
    created_by: string;
};

export function casoDeFila(f: FilaCaso): CasoQA {
    return {
        id: f.id,
        seccion: f.section,
        titulo: f.title,
        endpoint: f.location ?? undefined,
        precondiciones: JSON.parse(f.preconditions) as string[],
        pasos: JSON.parse(f.steps) as string[],
        datos: f.test_data ?? undefined,
        esperado: f.expected,
        severidad: f.severity,
        etiquetas: JSON.parse(f.tags) as string[],
        mock: Boolean(f.mock),
        ia_real: Boolean(f.ia_real),
        destructivo: Boolean(f.destructive),
        area: f.area,
        manual: true,
        activo: Boolean(f.active),
        creado_por: f.created_by,
    };
}

const DEL_REPO: CasoQA[] = catalogo.casos.map((c) => ({ ...c, area: "backend", manual: false, activo: true, creado_por: null }));

export type Casos = { lista: CasoQA[]; porId: Map<string, CasoQA> };

/** Casos del repo más los manuales (también los archivados: sus resultados y bugs los siguen nombrando). */
export async function cargarCasos(env: Env): Promise<Casos> {
    const { results } = await env.DB.prepare("SELECT * FROM qa_cases ORDER BY area, section COLLATE NOCASE, id").all<FilaCaso>();
    const lista = [...DEL_REPO, ...results.map(casoDeFila)];
    return { lista, porId: new Map(lista.map((c) => [c.id, c])) };
}

export async function obtenerCaso(env: Env, id: string): Promise<CasoQA | undefined> {
    const delRepo = DEL_REPO.find((c) => c.id === id);
    if (delRepo) return delRepo;
    const fila = await env.DB.prepare("SELECT * FROM qa_cases WHERE id = ?").bind(id).first<FilaCaso>();
    return fila ? casoDeFila(fila) : undefined;
}

/** Casos vigentes de un área (sin los archivados). */
export function casosDelArea(casos: Casos, area: Area): CasoQA[] {
    return casos.lista.filter((c) => c.area === area && c.activo);
}

/** Secciones (o casos de uso) de un área con cuántos casos vigentes tiene cada una. */
export function seccionesDelArea(casos: Casos, area: Area) {
    const titulos = new Map(catalogo.secciones.map((s) => [s.seccion, s.titulo]));
    const conteo = new Map<string, number>();
    for (const c of casosDelArea(casos, area)) conteo.set(c.seccion, (conteo.get(c.seccion) ?? 0) + 1);
    return [...conteo].map(([seccion, n]) => ({ seccion, titulo: titulos.get(seccion) ?? seccion, casos: n }));
}

/** Próximo id libre de un área: QA-FE-001, QA-FE-002… (nunca reutiliza uno archivado). */
export async function proximoId(env: Env, area: Area): Promise<string> {
    const prefijo = `QA-${PREFIJO[area]}-`;
    const fila = await env.DB.prepare("SELECT MAX(CAST(substr(id, ?) AS INTEGER)) AS n FROM qa_cases WHERE id LIKE ?")
        .bind(prefijo.length + 1, `${prefijo}%`)
        .first<{ n: number | null }>();
    return `${prefijo}${String((fila?.n ?? 0) + 1).padStart(3, "0")}`;
}
