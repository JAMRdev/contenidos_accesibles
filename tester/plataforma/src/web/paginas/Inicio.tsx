// Inicio: indicadores de sesiones y bugs, sesiones abiertas y estado de la plataforma.
import { useDatos } from "../api";
import { Estado as EstadoPlataforma } from "../Estado";
import { Link } from "../ruta";
import type { Avance, EstadoBug, Sesion } from "../tipos";
import { Alerta, Cargando, ENTORNO_TEXTO, fecha, Icon, Kpi } from "../ui";
import { BarraAvance } from "./Sesiones";

type ListaSesiones = { sesiones: (Sesion & { avance: Avance })[] };

export function Inicio() {
    const abiertas = useDatos<ListaSesiones>("/api/sesiones?estado=abierta&limite=12");
    const resumen = useDatos<{ por_estado: Record<EstadoBug, number> }>("/api/bugs/resumen");
    const n = (...estados: EstadoBug[]) => (resumen.datos ? estados.reduce((t, e) => t + resumen.datos!.por_estado[e], 0) : "…");

    return (
        <>
            <div className="admin-title-row">
                <div>
                    <h1 className="admin-h1">Plataforma de QA</h1>
                    <p className="admin-lead">Rondas de prueba del backend, evidencias y bugs publicados en GitHub.</p>
                </div>
            </div>
            <div className="kpi-grid">
                <Kpi icon="play_circle" label="Sesiones abiertas" valor={abiertas.datos?.sesiones.length ?? "…"} pie={<Link href="/sesiones">Ver sesiones</Link>} />
                <Kpi icon="bug_report" label="Bugs abiertos" valor={n("abierto", "en_curso", "reabierto")} pie={<Link href="/bugs?estado=abierto,en_curso,reabierto">Abiertos, en curso o reabiertos</Link>} />
                <Kpi icon="edit_note" label="Borradores sin publicar" valor={n("borrador")} pie={<Link href="/bugs?estado=borrador">Revisar y publicar</Link>} />
                <Kpi icon="task_alt" label="Resueltos para volver a probar" valor={n("resuelto")} pie={<Link href="/bugs?estado=resuelto">Verificar</Link>} />
            </div>

            <div className="admin-section-head">
                <div>
                    <h2 className="admin-h2">Sesiones abiertas</h2>
                    <p className="admin-muted admin-section-lead">Rondas de prueba en curso.</p>
                </div>
            </div>
            {abiertas.error && <Alerta tipo="danger">{abiertas.error}</Alerta>}
            {!abiertas.datos && !abiertas.error && <Cargando />}
            {abiertas.datos && !abiertas.datos.sesiones.length && (
                <p className="admin-note">
                    <Icon name="info" /> No hay sesiones abiertas. <Link href="/sesiones?nueva=1">Abrí una nueva</Link>.
                </p>
            )}
            {abiertas.datos && abiertas.datos.sesiones.length > 0 && (
                <div className="status-grid">
                    {abiertas.datos.sesiones.map((s) => (
                        <article key={s.id} className="status-card is-ok">
                            <div className="status-card-head">
                                <div>
                                    <h3 className="status-card-title">
                                        <Link href={`/sesiones/${s.id}`}>{s.titulo}</Link>
                                    </h3>
                                    <span className="cell-sub">
                                        {ENTORNO_TEXTO[s.entorno]} · {s.version ? `v${s.version}` : "sin versión"}
                                    </span>
                                </div>
                            </div>
                            <BarraAvance avance={s.avance} />
                            <p className="status-card-desc admin-muted">
                                {s.avance.total - s.avance.pendiente} de {s.avance.total} casos · abierta por @{s.creada_por} el {fecha(s.creada_en)}
                            </p>
                        </article>
                    ))}
                </div>
            )}

            <EstadoPlataforma />
        </>
    );
}
