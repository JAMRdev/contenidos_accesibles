// Cliente de la API del Worker: agrega el token de sesión de Clerk y convierte los errores en ApiError.
import { useAuth } from "@clerk/react";
import { createContext, useCallback, useContext, useEffect, useState } from "react";

export class ApiError extends Error {
    constructor(
        readonly status: number,
        message: string,
    ) {
        super(message);
    }
}

export type Rol = "viewer" | "qa" | "admin";

export type Yo = {
    github: string;
    nombre: string | null;
    avatar_url: string | null;
    rol: Rol;
    permisos: { escribir: boolean; administrar: boolean };
};

export function useApi() {
    const { getToken } = useAuth();
    return useCallback(
        async <T>(ruta: string, init: RequestInit = {}): Promise<T> => {
            const token = await getToken();
            const headers = new Headers(init.headers);
            if (token) headers.set("Authorization", `Bearer ${token}`);
            if (init.body) headers.set("Content-Type", "application/json");
            const res = await fetch(ruta, { ...init, headers });
            const data = (await res.json().catch(() => ({}))) as { error?: string };
            if (!res.ok) throw new ApiError(res.status, data.error ?? `Error ${res.status}`);
            return data as T;
        },
        [getToken],
    );
}

type Datos<T> = { datos: T | null; error: string | null; cargando: boolean; recargar: () => void; setDatos: (d: T) => void };

/** GET a la API que se repite cuando cambia la ruta; `recargar` lo vuelve a pedir. */
export function useDatos<T>(ruta: string | null): Datos<T> {
    const api = useApi();
    const [estado, setEstado] = useState<{ datos: T | null; error: string | null; cargando: boolean }>({ datos: null, error: null, cargando: true });
    const [vuelta, setVuelta] = useState(0);

    useEffect(() => {
        if (!ruta) return;
        let vigente = true;
        setEstado((e) => ({ ...e, cargando: true }));
        api<T>(ruta)
            .then((datos) => vigente && setEstado({ datos, error: null, cargando: false }))
            .catch((e: Error) => vigente && setEstado((s) => ({ ...s, error: e.message, cargando: false })));
        return () => {
            vigente = false;
        };
    }, [api, ruta, vuelta]);

    return {
        ...estado,
        recargar: useCallback(() => setVuelta((v) => v + 1), []),
        setDatos: useCallback((datos: T) => setEstado({ datos, error: null, cargando: false }), []),
    };
}

// Quién está usando la web (lo carga App al entrar)
export const YoContext = createContext<Yo | null>(null);

export function useYo(): Yo {
    const yo = useContext(YoContext);
    if (!yo) throw new Error("useYo fuera de YoContext");
    return yo;
}
