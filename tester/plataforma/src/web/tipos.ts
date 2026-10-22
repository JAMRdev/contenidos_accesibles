// Tipos de las respuestas de la API que usa la web.
import type { Caso, Seccion } from "../catalogo/esquema.ts";

export type { Caso, Seccion };

export type Entorno = "produccion" | "staging" | "local";
export type EstadoResultado = "paso" | "fallo" | "bloqueado" | "omitido";
export type EstadoBug = "borrador" | "abierto" | "en_curso" | "resuelto" | "verificado" | "reabierto" | "descartado";
export type Severidad = "alta" | "media" | "baja";

export type Avance = Record<EstadoResultado | "pendiente" | "total", number>;

export type Sesion = {
    id: number;
    titulo: string;
    entorno: Entorno;
    url: string;
    version: string | null;
    notas: string | null;
    estado: "abierta" | "cerrada";
    creada_por: string;
    creada_en: string;
    cerrada_por: string | null;
    cerrada_en: string | null;
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
    entorno: Entorno | null;
    titulo: string;
    descripcion: string;
    severidad: Severidad;
    estado: EstadoBug;
    issue: { numero: number; url: string; estado: string | null; motivo: string | null; tablero: string | null } | null;
    sincronizado_en: string | null;
    creado_por: string;
    creado_en: string;
    actualizado_en: string;
};

export type BugDetalle = Bug & {
    historial: { de: EstadoBug | null; a: EstadoBug; origen: "qa" | "github" | "sincronizacion"; actor: string | null; nota: string | null; en: string }[];
    comentarios: Comentario[];
};
