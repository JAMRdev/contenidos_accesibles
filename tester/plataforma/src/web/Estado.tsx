// Estado de la plataforma (GET /health, sin login), con las tarjetas de la pestaña Estado del panel.
import { useEffect, useState } from "react";
import { Alerta, Icon } from "./ui";

type Health = {
    ok: boolean;
    version: string;
    d1: string;
    r2: string;
    ultima_cron: string | null;
    ultima_sincronizacion: string | null;
    casos: number;
};

type State = { kind: "loading" } | { kind: "ready"; health: Health } | { kind: "error"; message: string };

const ESTADO = {
    ok: { icon: "check_circle", label: "Operativo", css: "active" },
    down: { icon: "error", label: "Caído", css: "exhausted" },
    pendiente: { icon: "do_not_disturb_on", label: "Sin datos", css: "disabled" },
} as const;

const fecha = new Intl.DateTimeFormat("es-AR", { dateStyle: "short", timeStyle: "short" });

function Tarjeta(props: { titulo: string; plataforma: string; estado: keyof typeof ESTADO; detalle: string; descripcion: string }) {
    const st = ESTADO[props.estado];
    const clase = props.estado === "pendiente" ? "" : ` is-${props.estado}`;
    return (
        <article className={`status-card${clase}`}>
            <div className="status-card-head">
                <div>
                    <h3 className="status-card-title">{props.titulo}</h3>
                    <span className="cell-sub">{props.plataforma}</span>
                </div>
                <span className={`status status-${st.css}`}>
                    <Icon name={st.icon} />
                    {st.label}
                </span>
            </div>
            <p className="status-card-detail">{props.detalle}</p>
            <p className="status-card-desc admin-muted">{props.descripcion}</p>
        </article>
    );
}

export function Estado() {
    const [state, setState] = useState<State>({ kind: "loading" });

    useEffect(() => {
        fetch("/health")
            .then(async (res) => setState({ kind: "ready", health: (await res.json()) as Health }))
            .catch(() => setState({ kind: "error", message: "No se pudo consultar el estado de la plataforma." }));
    }, []);

    return (
        <section aria-labelledby="estado" aria-busy={state.kind === "loading"}>
            <div className="admin-section-head">
                <div>
                    <h2 className="admin-h2" id="estado">
                        Estado de la plataforma
                    </h2>
                    <p className="admin-muted admin-section-lead">Servicios propios de QA en Cloudflare, separados del backend de Railway.</p>
                </div>
            </div>
            {state.kind === "loading" && <p className="admin-muted">Consultando…</p>}
            {state.kind === "error" && <Alerta tipo="danger">{state.message}</Alerta>}
            {state.kind === "ready" && (
                <>
                    <div className="status-overall">
                        <Alerta tipo={state.health.ok ? "success" : "danger"}>
                            {state.health.ok ? "La plataforma funciona." : "La plataforma tiene problemas: revisá las tarjetas."}
                        </Alerta>
                    </div>
                    <div className="status-grid">
                        <Tarjeta
                            titulo="Base de datos"
                            plataforma="Cloudflare D1"
                            estado={state.health.d1 === "ok" ? "ok" : "down"}
                            detalle={state.health.d1 === "ok" ? "Consulta de prueba OK" : `Falló (${state.health.d1})`}
                            descripcion="Sesiones, resultados, bugs, miembros y auditoría."
                        />
                        <Tarjeta
                            titulo="Evidencias"
                            plataforma="Cloudflare R2"
                            estado={state.health.r2 === "ok" ? "ok" : "down"}
                            detalle={state.health.r2 === "ok" ? "Bucket accesible" : `Falló (${state.health.r2})`}
                            descripcion="Capturas, videos y archivos de los resultados. Sin borrado automático."
                        />
                        <Tarjeta
                            titulo="Catálogo de casos"
                            plataforma="tester/casos"
                            estado="ok"
                            detalle={`${state.health.casos} casos`}
                            descripcion={`Empaquetado en el deploy · versión ${state.health.version}.`}
                        />
                        <Tarjeta
                            titulo="Sincronización"
                            plataforma="GitHub · Cron cada 10 min"
                            estado={state.health.ultima_sincronizacion ? "ok" : "pendiente"}
                            detalle={state.health.ultima_sincronizacion ? `Última: ${fecha.format(new Date(state.health.ultima_sincronizacion))}` : "Todavía no corrió"}
                            descripcion="Trae de GitHub el estado, el Status del tablero y los comentarios de los bugs publicados."
                        />
                    </div>
                </>
            )}
        </section>
    );
}
