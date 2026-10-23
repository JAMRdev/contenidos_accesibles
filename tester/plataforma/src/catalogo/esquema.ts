// Esquema de los casos de prueba del catálogo (tester/casos/*.yml, Issue #125).
// Lo usan el validador (scripts/catalogo.ts) y el Worker (tipos del catálogo empaquetado).
import { z } from "zod";

// QA-<sección de la guía>-NN: QA-4.8-03 (sección 4.8) o QA-5-01 (sección 5)
export const ID_CASO = /^QA-(\d+(?:\.\d+)?)-(\d{2})$/;
export const ID_CASO_EN_TEXTO = /QA-\d+(?:\.\d+)?-\d{2}/g;
export const SEVERIDADES = ["alta", "media", "baja"] as const;

const texto = z.string().trim().min(1);

export const casoSchema = z
    .strictObject({
        id: z.string().regex(ID_CASO, "El id tiene que ser QA-<sección>-NN, por ejemplo QA-4.8-03."),
        titulo: texto,
        // "POST /api/v1/analysis/sample". Los flujos de punta a punta no tienen uno solo.
        endpoint: texto.optional(),
        precondiciones: z.array(texto).default([]),
        pasos: z.array(texto).min(1),
        // Datos de prueba de la sección 3 de la guía (nunca datos reales)
        datos: texto.optional(),
        esperado: texto,
        // Prioridad del bug si el caso falla (sección 9 de la guía)
        severidad: z.enum(SEVERIDADES),
        etiquetas: z.array(z.string().regex(/^[a-z0-9-]+$/, "Etiquetas en minúsculas, con guiones.")).default([]),
        // Se prueba con ?mock=true (no gasta IA)
        mock: z.boolean().default(false),
        // Consume IA real: coordinar con Backend antes de probarlo
        ia_real: z.boolean().default(false),
        // Solo en local o staging, nunca en producción (estrés, cambios de configuración)
        destructivo: z.boolean().default(false),
    })
    .refine((c) => !(c.mock && c.ia_real), { message: "Un caso no puede ser mock e IA real a la vez.", path: ["mock"] });

export const archivoSchema = z.strictObject({
    seccion: z.string().regex(/^\d+(?:\.\d+)?$/, "La sección es el número de la guía, por ejemplo 4.8."),
    titulo: texto,
    casos: z.array(casoSchema).min(1),
});

export type Caso = z.output<typeof casoSchema> & { seccion: string };

export type Seccion = { seccion: string; titulo: string; archivo: string; casos: number };

// Lo que se empaqueta en el Worker (src/worker/catalogo.gen.json)
export type Catalogo = { secciones: Seccion[]; casos: Caso[] };
