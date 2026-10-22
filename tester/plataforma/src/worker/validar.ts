// Validación de bodies y parámetros de consulta de la API. Los errores se lanzan como
// HTTPException y index.ts los responde como { error } con su código.
import type { Context } from "hono";
import { HTTPException } from "hono/http-exception";
import { z } from "zod";

export function error(status: 403 | 404 | 409 | 422, mensaje: string): HTTPException {
    return new HTTPException(status, { message: mensaje });
}

/** Body JSON validado con el esquema; lanza 422 con el primer error. */
export async function leerBody<T extends z.ZodType>(c: Context, esquema: T): Promise<z.output<T>> {
    const resultado = esquema.safeParse(await c.req.json().catch(() => null));
    if (!resultado.success) {
        const issue = resultado.error.issues[0];
        const campo = issue?.path.length ? `${issue.path.join(".")}: ` : "";
        throw error(422, issue ? `${campo}${issue.message}` : "Datos inválidos.");
    }
    return resultado.data;
}

/** Id numérico de la ruta; 404 si no es un número. */
export function idDe(valor: string, que: string): number {
    const id = Number(valor);
    if (!Number.isInteger(id) || id <= 0) throw error(404, `${que} no encontrado.`);
    return id;
}

/** "true"/"si" → true, "false"/"no" → false, cualquier otra cosa → sin filtro. */
export function consultaBooleana(valor: string | undefined): boolean | undefined {
    if (valor === "true" || valor === "si") return true;
    if (valor === "false" || valor === "no") return false;
    return undefined;
}

/** Lista separada por comas, solo con los valores permitidos. */
export function consultaLista<T extends string>(valor: string | undefined, permitidos: readonly T[]): T[] {
    return (valor ?? "")
        .split(",")
        .map((v) => v.trim())
        .filter((v): v is T => (permitidos as readonly string[]).includes(v));
}

/** WHERE armado con condiciones parametrizadas (los valores nunca se concatenan al SQL). */
export class Filtros {
    private condiciones: string[] = [];
    readonly valores: unknown[] = [];

    agregar(condicion: string, ...valores: unknown[]): void {
        this.condiciones.push(condicion);
        this.valores.push(...valores);
    }

    /** columna IN (...); no filtra si la lista está vacía. `columna` es siempre un literal del código. */
    en(columna: string, lista: readonly unknown[]): void {
        if (lista.length) this.agregar(`${columna} IN (${lista.map(() => "?").join(", ")})`, ...lista);
    }

    sql(): string {
        return this.condiciones.length ? `WHERE ${this.condiciones.join(" AND ")}` : "";
    }
}

export function limiteDe(valor: string | undefined, porDefecto = 50, maximo = 200): number {
    return Math.min(Math.max(Number(valor) || porDefecto, 1), maximo);
}

export const textoOpcional = (max: number) =>
    z
        .string()
        .trim()
        .max(max, `Hasta ${max} caracteres.`)
        .transform((v) => v || null)
        .nullish();
