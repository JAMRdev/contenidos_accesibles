// Miembros de la plataforma (solo admins): agregar, cambiar el rol, suspender y quitar.
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ROL_TEXTO, useApi, type Rol } from "./api";

type Miembro = { github: string; rol: Rol; habilitado: boolean; agregado_por: string | null };
type Listado = { fijos: { github: string; rol: Rol }[]; miembros: Miembro[] };

const ROLES: Rol[] = ["viewer", "qa", "admin"];

export function Miembros({ yo }: { yo: string }) {
    const api = useApi();
    const [listado, setListado] = useState<Listado | null>(null);
    const [aviso, setAviso] = useState<{ tipo: "ok" | "error"; texto: string } | null>(null);
    const [nuevo, setNuevo] = useState({ github: "", rol: "qa" as Rol });

    const ejecutar = useCallback(
        async (accion: () => Promise<Listado>, ok?: string) => {
            try {
                setListado(await accion());
                setAviso(ok ? { tipo: "ok", texto: ok } : null);
                return true;
            } catch (e) {
                setAviso({ tipo: "error", texto: (e as Error).message });
                return false;
            }
        },
        [],
    );

    useEffect(() => {
        void ejecutar(() => api<Listado>("/api/miembros"));
    }, [api, ejecutar]);

    async function agregar(ev: FormEvent) {
        ev.preventDefault();
        const github = nuevo.github.trim().replace(/^@/, "");
        if (!github) return setAviso({ tipo: "error", texto: "Escribí el usuario de GitHub." });
        const ok = await ejecutar(
            () => api<Listado>("/api/miembros", { method: "POST", body: JSON.stringify({ github, rol: nuevo.rol }) }),
            `@${github} ya tiene acceso.`,
        );
        if (ok) setNuevo({ ...nuevo, github: "" });
    }

    const cambiar = (m: Miembro, cambios: Partial<{ rol: Rol; habilitado: boolean }>, ok: string) =>
        ejecutar(() => api<Listado>(`/api/miembros/${encodeURIComponent(m.github)}`, { method: "PATCH", body: JSON.stringify(cambios) }), ok);

    const quitar = (m: Miembro) => {
        if (!window.confirm(`¿Quitar el acceso de @${m.github}?`)) return;
        void ejecutar(() => api<Listado>(`/api/miembros/${encodeURIComponent(m.github)}`, { method: "DELETE" }), `Se quitó el acceso de @${m.github}.`);
    };

    return (
        <section className="card" aria-labelledby="miembros">
            <h2 id="miembros">Miembros</h2>
            <p className="muted small">
                Solo lectura ve todo; QA además registra resultados y publica; admin además administra miembros.
            </p>

            <form className="inline-form" onSubmit={agregar}>
                <label>
                    Usuario de GitHub
                    <input value={nuevo.github} onChange={(e) => setNuevo({ ...nuevo, github: e.target.value })} placeholder="@usuario" autoComplete="off" />
                </label>
                <label>
                    Rol
                    <select value={nuevo.rol} onChange={(e) => setNuevo({ ...nuevo, rol: e.target.value as Rol })}>
                        {ROLES.map((r) => (
                            <option key={r} value={r}>
                                {ROL_TEXTO[r]}
                            </option>
                        ))}
                    </select>
                </label>
                <button type="submit">Dar acceso</button>
            </form>

            <div aria-live="polite">{aviso && <p className={aviso.tipo === "ok" ? "notice ok" : "notice bad"} role={aviso.tipo === "error" ? "alert" : "status"}>{aviso.texto}</p>}</div>

            {listado && (
                <div className="table-wrap">
                    <table>
                        <thead>
                            <tr>
                                <th scope="col">Usuario</th>
                                <th scope="col">Rol</th>
                                <th scope="col">Acceso</th>
                                <th scope="col">
                                    <span className="visually-hidden">Acciones</span>
                                </th>
                            </tr>
                        </thead>
                        <tbody>
                            {listado.fijos.map((f) => (
                                <tr key={f.github}>
                                    <td>@{f.github}</td>
                                    <td>{ROL_TEXTO[f.rol]}</td>
                                    <td>Fijo</td>
                                    <td className="muted small">Se cambia en QA_ADMINS</td>
                                </tr>
                            ))}
                            {listado.miembros.map((m) => {
                                const soyYo = m.github.toLowerCase() === yo.toLowerCase();
                                return (
                                    <tr key={m.github} className={m.habilitado ? undefined : "is-disabled"}>
                                        <td>@{m.github}</td>
                                        <td>
                                            <select
                                                aria-label={`Rol de @${m.github}`}
                                                value={m.rol}
                                                disabled={soyYo}
                                                onChange={(e) => void cambiar(m, { rol: e.target.value as Rol }, `@${m.github} ahora es ${ROL_TEXTO[e.target.value as Rol]}.`)}
                                            >
                                                {ROLES.map((r) => (
                                                    <option key={r} value={r}>
                                                        {ROL_TEXTO[r]}
                                                    </option>
                                                ))}
                                            </select>
                                        </td>
                                        <td>
                                            <label className="check">
                                                <input
                                                    type="checkbox"
                                                    checked={m.habilitado}
                                                    disabled={soyYo}
                                                    onChange={(e) => void cambiar(m, { habilitado: e.target.checked }, `@${m.github}: acceso ${e.target.checked ? "habilitado" : "suspendido"}.`)}
                                                />
                                                {m.habilitado ? "Activo" : "Suspendido"}
                                            </label>
                                        </td>
                                        <td>
                                            {soyYo ? (
                                                <span className="muted small">Tu acceso lo cambia otro admin</span>
                                            ) : (
                                                <button type="button" className="danger" onClick={() => quitar(m)}>
                                                    Quitar
                                                </button>
                                            )}
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </section>
    );
}
