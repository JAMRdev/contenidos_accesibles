// Web de QA. Por ahora muestra el estado del Worker; el login con Clerk llega con #126
// y las pantallas de sesiones, casos y bugs con #131.
import { useEffect, useState } from "react";

type Health = {
    ok: boolean;
    version: string;
    d1: string;
    r2: string;
    ultima_cron: string | null;
};

type State = { kind: "loading" } | { kind: "ready"; health: Health } | { kind: "error"; message: string };

function formatDate(iso: string | null): string {
    if (!iso) return "Todavía no corrió";
    return new Date(iso).toLocaleString("es-AR", { dateStyle: "short", timeStyle: "short" });
}

export function App() {
    const [state, setState] = useState<State>({ kind: "loading" });

    useEffect(() => {
        fetch("/health")
            .then(async (res) => setState({ kind: "ready", health: (await res.json()) as Health }))
            .catch(() => setState({ kind: "error", message: "No se pudo consultar el estado de la plataforma." }));
    }, []);

    return (
        <main className="page">
            <header>
                <h1>Plataforma de QA</h1>
                <p className="muted">Backend de InnovaLab · contenidos accesibles</p>
            </header>

            <section className="card" aria-labelledby="estado" aria-busy={state.kind === "loading"}>
                <h2 id="estado">Estado</h2>
                {state.kind === "loading" && <p>Consultando…</p>}
                {state.kind === "error" && <p role="alert">{state.message}</p>}
                {state.kind === "ready" && (
                    <dl className="facts">
                        <dt>General</dt>
                        <dd className={state.health.ok ? "ok" : "bad"}>{state.health.ok ? "En línea" : "Con problemas"}</dd>
                        <dt>Base de datos (D1)</dt>
                        <dd>{state.health.d1}</dd>
                        <dt>Evidencias (R2)</dt>
                        <dd>{state.health.r2}</dd>
                        <dt>Última sincronización</dt>
                        <dd>{formatDate(state.health.ultima_cron)}</dd>
                        <dt>Versión</dt>
                        <dd>
                            <code>{state.health.version}</code>
                        </dd>
                    </dl>
                )}
            </section>
        </main>
    );
}
