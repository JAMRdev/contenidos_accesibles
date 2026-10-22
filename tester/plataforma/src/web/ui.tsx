// Piezas de interfaz compartidas, con el mismo markup y las mismas clases que el panel /admin.
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import type { Rol } from "./api";
import type { Entorno, EstadoBug, EstadoResultado, Severidad } from "./tipos";

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

// Estados con ícono + texto (clases .status del panel)
export const RESULTADO_INFO: Record<EstadoResultado | "pendiente", { icon: string; label: string; css: string }> = {
    pendiente: { icon: "pending", label: "Pendiente", css: "standby" },
    paso: { icon: "check_circle", label: "Pasó", css: "active" },
    fallo: { icon: "cancel", label: "Falló", css: "exhausted" },
    bloqueado: { icon: "block", label: "Bloqueado", css: "near-limit" },
    omitido: { icon: "skip_next", label: "Omitido", css: "disabled" },
};

export const BUG_INFO: Record<EstadoBug, { icon: string; label: string; css: string }> = {
    borrador: { icon: "edit_note", label: "Borrador", css: "standby" },
    abierto: { icon: "bug_report", label: "Abierto", css: "exhausted" },
    en_curso: { icon: "construction", label: "En curso", css: "near-limit" },
    resuelto: { icon: "task_alt", label: "Resuelto", css: "active" },
    verificado: { icon: "verified", label: "Verificado", css: "active" },
    reabierto: { icon: "replay", label: "Reabierto", css: "exhausted" },
    descartado: { icon: "do_not_disturb_on", label: "Descartado", css: "disabled" },
};

export const ENTORNO_TEXTO: Record<Entorno, string> = { produccion: "Producción", staging: "Staging", local: "Local" };
export const SEVERIDAD_TEXTO: Record<Severidad, string> = { alta: "Alta", media: "Media", baja: "Baja" };

export function Estado({ info }: { info: { icon: string; label: string; css: string } }) {
    return (
        <span className={`status status-${info.css}`}>
            <Icon name={info.icon} />
            {info.label}
        </span>
    );
}

export function Marcas({ caso }: { caso: { mock: boolean; ia_real: boolean; destructivo: boolean } }) {
    return (
        <span className="qa-flags">
            {caso.mock && <span className="badge badge-s-info badge-sm">mock</span>}
            {caso.ia_real && <span className="badge badge-s-warning badge-sm">IA real</span>}
            {caso.destructivo && <span className="badge badge-s-danger badge-sm">solo local</span>}
        </span>
    );
}

const fechaCorta = new Intl.DateTimeFormat("es-AR", { dateStyle: "short", timeStyle: "short" });
export function fecha(iso: string | null): string {
    return iso ? fechaCorta.format(new Date(iso)) : "—";
}

export function Kpi({ icon, label, valor, pie }: { icon: string; label: string; valor: ReactNode; pie?: ReactNode }) {
    return (
        <div className="kpi">
            <span className="kpi-label">
                <Icon name={icon} />
                {label}
            </span>
            <span className="kpi-value">{valor}</span>
            {pie && <div className="kpi-foot"><span className="kpi-delta">{pie}</span></div>}
        </div>
    );
}

/** Selector nativo con el aspecto de Obelisco y su etiqueta (o aria-label si no hay etiqueta visible). */
export function Selector<T extends string>(props: {
    id: string;
    etiqueta: string;
    valor: T;
    opciones: readonly (readonly [T, string])[];
    onChange: (v: T) => void;
    oculta?: boolean;
    chico?: boolean;
    disabled?: boolean;
}) {
    const select = (
        <select
            id={props.id}
            className={`qa-select${props.chico ? " qa-select-sm" : ""}`}
            aria-label={props.oculta ? props.etiqueta : undefined}
            value={props.valor}
            disabled={props.disabled}
            onChange={(e) => props.onChange(e.target.value as T)}
        >
            {props.opciones.map(([v, texto]) => (
                <option key={v} value={v}>
                    {texto}
                </option>
            ))}
        </select>
    );
    if (props.oculta) return select;
    return (
        <div className="form-group">
            <div className="form-label-container">
                <label className="form-label" htmlFor={props.id}>
                    {props.etiqueta}
                </label>
            </div>
            {select}
        </div>
    );
}

export function Busqueda({ id, etiqueta, valor, onChange, placeholder }: { id: string; etiqueta: string; valor: string; onChange: (v: string) => void; placeholder?: string }) {
    const [texto, setTexto] = useState(valor);
    useEffect(() => setTexto(valor), [valor]);
    // Espera a que se deje de escribir para no pedir en cada tecla
    useEffect(() => {
        if (texto === valor) return;
        const t = setTimeout(() => onChange(texto), 300);
        return () => clearTimeout(t);
    }, [texto, valor, onChange]);
    return (
        <div className="form-group">
            <div className="form-label-container">
                <label className="form-label" htmlFor={id}>
                    {etiqueta}
                </label>
            </div>
            <div className="search-field">
                <Icon name="search" />
                <input className="form-control" id={id} type="search" value={texto} placeholder={placeholder} onChange={(e) => setTexto(e.target.value)} />
            </div>
        </div>
    );
}

export function Cargando({ texto = "Cargando…" }: { texto?: string }) {
    return (
        <p className="admin-muted" aria-busy="true">
            <span className="spinner-border spinner-border-sm me-2" aria-hidden="true" />
            {texto}
        </p>
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
