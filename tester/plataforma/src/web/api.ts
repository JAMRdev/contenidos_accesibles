// Cliente de la API del Worker: agrega el token de sesión de Clerk y convierte los errores en ApiError.
import { useAuth } from "@clerk/react";
import { useCallback } from "react";

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

export const ROL_TEXTO: Record<Rol, string> = { viewer: "Solo lectura", qa: "QA", admin: "Admin" };

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
