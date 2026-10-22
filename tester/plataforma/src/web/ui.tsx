// Piezas de interfaz compartidas, con el mismo markup y las mismas clases que el panel /admin.
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import type { Rol } from "./api";

export function Icon({ name }: { name: string }) {
    return (
        <span className="material-symbols-rounded" aria-hidden="true">
            {name}
        </span>
    );
}

export const ROL_INFO: Record<Rol, { icon: string; label: string }> = {
    admin: { icon: "shield_person", label: "Admin" },
    qa: { icon: "fact_check", label: "QA" },
    viewer: { icon: "visibility", label: "Solo lectura" },
};

export function RoleBadge({ rol }: { rol: Rol }) {
    const info = ROL_INFO[rol];
    return (
        <span className={`role-badge role-${rol}`}>
            <Icon name={info.icon} />
            {info.label}
        </span>
    );
}

export function Marca({ subtitulo = "Plataforma de QA" }: { subtitulo?: string }) {
    return (
        <>
            <span className="material-symbols-rounded admin-brand-icon" aria-hidden="true">
                accessibility_new
            </span>
            <span>
                <span className="admin-brand-title">Contenidos accesibles</span>
                <span className="admin-brand-subtitle">{subtitulo}</span>
            </span>
        </>
    );
}

export function Alerta({ tipo, children }: { tipo: "danger" | "success" | "warning" | "info"; children: ReactNode }) {
    return (
        <div className={`alert alert-${tipo}`} role={tipo === "danger" ? "alert" : "status"}>
            <p className="mb-0">{children}</p>
        </div>
    );
}

// Aviso flotante, como el toast del panel
const ToastContext = createContext<(texto: string) => void>(() => {});

export function useToast() {
    return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: ReactNode }) {
    const [texto, setTexto] = useState<string | null>(null);
    const mostrar = useCallback((t: string) => setTexto(t), []);

    useEffect(() => {
        if (!texto) return;
        const id = setTimeout(() => setTexto(null), 4000);
        return () => clearTimeout(id);
    }, [texto]);

    return (
        <ToastContext.Provider value={mostrar}>
            {children}
            <div className="qa-toast" aria-live="polite" aria-atomic="true">
                {texto && (
                    <div className="toast show" role="status">
                        <div className="toast-body">{texto}</div>
                    </div>
                )}
            </div>
        </ToastContext.Provider>
    );
}
