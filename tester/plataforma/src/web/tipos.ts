// Tipos de las respuestas de la API que usa la web.
import type { Caso as CasoRepo, Seccion as SeccionRepo } from "../catalogo/esquema.ts";

export type Area = "backend" | "frontend";

/** Un caso del repo (área backend) o uno manual creado desde la plataforma. */
export type Caso = CasoRepo & { area: Area; manual: boolean; activo: boolean; creado_por: string | null };

export type Seccion = Pick<SeccionRepo, "seccion" | "titulo" | "casos">;

export type Entorno = "produccion" | "staging" | "local";
export type EstadoResultado = "paso" | "fallo" | "bloqueado" | "omitido";
export type EstadoBug = "borrador" | "abierto" | "en_curso" | "resuelto" | "verificado" | "reabierto" | "descartado";
export type Severidad = "alta" | "media" | "baja";

export type Avance = Record<EstadoResultado | "pendiente" | "total", number>;

export type Sesion = {
    id: number;
    titulo: string;
    area: Area;
    entorno: Entorno;
    url: string;
    version: string | null;
    notas: string | null;
    estado: "abierta" | "cerrada";
    creada_por: string;
    creada_en: string;
    cerrada_por: string | null;
    cerrada_en: string | null;
    ejecucion: { numero: number; url: string } | null;
};

export type ResultadoCaso = {
    caso: Caso;
    aplica: boolean;
    resultado: { id: number; estado: EstadoResultado; notas: string | null; actualizado_por: string; actualizado_en: string } | null;
};

export type Comentario = { id: number; autor: string; texto: string; origen: "plataforma" | "github"; en_github: boolean; publicado_en_github: boolean; creado_en: string };

export type Bug = {
    id: number;
    caso: { id: string; seccion: string; titulo: string | null };
    sesion_id: number | null;
    area: Area;
    entorno: Entorno | null;
    titulo: string;
    descripcion: string;
    severidad: Severidad;
    reporte: Reporte;
    estado: EstadoBug;
    issue: { numero: number; url: string; estado: string | null; motivo: string | null; tablero: string | null } | null;
    sincronizado_en: string | null;
    creado_por: string;
    creado_en: string;
    actualizado_en: string;
};

export type Reporte = { pasos: string; esperado: string; obtenido: string; url: string | null; version: string | null };

export type VistaPrevia = { titulo: string; cuerpo: string; etiquetas: string[]; repo: string; faltan: string[] };

export type Duplicados = {
    plataforma: Bug[];
    github: { disponible: boolean; issues: { numero: number; titulo: string; url: string; estado: string }[]; error: string | null };
};

export type BugDetalle = Bug & {
    historial: { de: EstadoBug | null; a: EstadoBug; origen: "qa" | "github" | "sincronizacion"; actor: string | null; nota: string | null; en: string }[];
    comentarios: Comentario[];
};
