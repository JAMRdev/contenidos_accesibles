// Rutas de la web sin dependencias: History API + el fallback de SPA del Worker (assets).
import { createContext, useCallback, useContext, useEffect, useState, type AnchorHTMLAttributes, type MouseEvent, type ReactNode } from "react";

type Ruta = { path: string; query: URLSearchParams; ir: (destino: string, opciones?: { reemplazar?: boolean }) => void };

const RutaContext = createContext<Ruta | null>(null);

export function RutaProvider({ children }: { children: ReactNode }) {
    const [lugar, setLugar] = useState(() => ({ path: location.pathname, search: location.search }));

    useEffect(() => {
        const alVolver = () => setLugar({ path: location.pathname, search: location.search });
        addEventListener("popstate", alVolver);
        return () => removeEventListener("popstate", alVolver);
    }, []);

    const ir = useCallback((destino: string, opciones?: { reemplazar?: boolean }) => {
        const url = new URL(destino, location.origin);
        history[opciones?.reemplazar ? "replaceState" : "pushState"](null, "", url.pathname + url.search);
        setLugar({ path: url.pathname, search: url.search });
        if (!opciones?.reemplazar) scrollTo(0, 0);
    }, []);

    return <RutaContext.Provider value={{ path: lugar.path, query: new URLSearchParams(lugar.search), ir }}>{children}</RutaContext.Provider>;
}

export function useRuta(): Ruta {
    const ruta = useContext(RutaContext);
    if (!ruta) throw new Error("useRuta fuera de RutaProvider");
    return ruta;
}

/** Enlace interno: navega sin recargar (Ctrl/Cmd + clic sigue abriendo otra pestaña). */
export function Link({ href, onClick, ...props }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) {
    const { ir } = useRuta();
    const alHacerClic = (ev: MouseEvent<HTMLAnchorElement>) => {
        onClick?.(ev);
        if (ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
        ev.preventDefault();
        ir(href);
    };
    return <a href={href} onClick={alHacerClic} {...props} />;
}

/** Filtros guardados en la URL (?clave=valor), así se pueden compartir y sobreviven al recargar. */
export function useFiltros<T extends Record<string, string>>(porDefecto: T): [T, (cambios: Partial<T>) => void] {
    const { path, query, ir } = useRuta();
    const valores = Object.fromEntries(Object.entries(porDefecto).map(([k, v]) => [k, query.get(k) ?? v])) as T;
    const cambiar = (cambios: Partial<T>) => {
        const nueva = new URLSearchParams(query);
        for (const [k, v] of Object.entries(cambios)) {
            if (v) nueva.set(k, v as string);
            else nueva.delete(k);
        }
        const texto = nueva.toString();
        ir(texto ? `${path}?${texto}` : path, { reemplazar: true });
    };
    return [valores, cambiar];
}
