// Catálogo de casos empaquetado en el build (npm run catalogo genera catalogo.gen.json desde
// tester/casos/*.yml). Cambia solo con un deploy.
import type { Caso, Catalogo } from "../catalogo/esquema.ts";
import datos from "./catalogo.gen.json";

export const catalogo = datos as Catalogo;

const porId = new Map<string, Caso>(catalogo.casos.map((c) => [c.id, c]));

export function buscarCaso(id: string): Caso | undefined {
    return porId.get(id);
}

export const ENTORNOS = ["produccion", "staging", "local"] as const;
export type Entorno = (typeof ENTORNOS)[number];

/** Los casos destructivos (estrés, cambios de configuración) no se prueban en producción. */
export function aplica(caso: Caso, entorno: Entorno): boolean {
    return entorno !== "produccion" || !caso.destructivo;
}

export type FiltroCasos = {
    seccion?: string;
    etiqueta?: string;
    q?: string;
    destructivo?: boolean;
    mock?: boolean;
    ia_real?: boolean;
};

/** Filtra una lista de casos (por defecto, los del repo). */
export function filtrarCasos<T extends Caso>(f: FiltroCasos, lista: T[] = catalogo.casos as T[]): T[] {
    const q = f.q?.trim().toLowerCase();
    return lista.filter(
        (c) =>
            (!f.seccion || c.seccion === f.seccion) &&
            (!f.etiqueta || c.etiquetas.includes(f.etiqueta)) &&
            (f.destructivo === undefined || c.destructivo === f.destructivo) &&
            (f.mock === undefined || c.mock === f.mock) &&
            (f.ia_real === undefined || c.ia_real === f.ia_real) &&
            (!q || `${c.id} ${c.titulo} ${c.endpoint ?? ""} ${c.esperado}`.toLowerCase().includes(q)),
    );
}
