// Una evidencia a pantalla completa. Es la página que se enlaza desde los issues de GitHub:
// como toda la web, pide iniciar sesión.
import { useAuth } from "@clerk/react";
import { useDatos } from "../api";
import { tamano, Vista, type Evidencia } from "../Evidencias";
import { Link } from "../ruta";
import { Alerta, Cargando, fecha, Icon } from "../ui";

export function EvidenciaPagina({ id }: { id: string }) {
    const { getToken } = useAuth();
    const ev = useDatos<Evidencia>(`/api/evidencias/${id}`);
    if (ev.error) return <Alerta tipo="danger">{ev.error}</Alerta>;
    if (!ev.datos) return <Cargando />;
    const e = ev.datos;

    async function descargar() {
        const res = await fetch(`/api/evidencias/${e.id}/archivo`, { headers: { Authorization: `Bearer ${await getToken()}` } });
        const url = URL.createObjectURL(await res.blob());
        const a = Object.assign(document.createElement("a"), { href: url, download: e.nombre });
        a.click();
        URL.revokeObjectURL(url);
    }

    return (
        <>
            <nav aria-label="Ruta" className="admin-breadcrumb">
                {e.bug_id ? <Link href={`/bugs/${e.bug_id}`}>Bug #{e.bug_id}</Link> : <span>Resultado #{e.resultado_id}</span>} / Evidencia #{e.id}
            </nav>
            <div className="admin-section-head">
                <div>
                    <h1 className="admin-h2">{e.etiqueta ?? e.nombre}</h1>
                    <p className="admin-muted admin-section-lead">
                        {e.nombre} · {tamano(e.bytes)} · subida por @{e.subida_por} el {fecha(e.subida_en)}
                        {e.datos_ocultos > 0 && ` · se ocultaron ${e.datos_ocultos} dato(s) sensible(s)`}
                    </p>
                </div>
                <button type="button" className="btn btn-outline-primary" onClick={() => void descargar()}>
                    <Icon name="download" /> Descargar
                </button>
            </div>
            <div className="qa-card">
                <Vista ev={e} />
            </div>
        </>
    );
}
