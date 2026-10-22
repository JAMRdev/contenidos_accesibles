// Miembros de la plataforma (solo admins), con el mismo diseño que la pestaña Usuarios del panel.
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useApi, type Rol } from "./api";
import { Alerta, Icon, ROL_INFO, RoleBadge, useToast } from "./ui";

type Miembro = { github: string; rol: Rol; habilitado: boolean; agregado_por: string | null };
type Listado = { fijos: { github: string; rol: Rol }[]; miembros: Miembro[] };

const ROLES: Rol[] = ["viewer", "qa", "admin"];

function SelectorRol(props: { id: string; valor: Rol; onChange: (r: Rol) => void; etiqueta?: string; chico?: boolean; disabled?: boolean }) {
    return (
        <select
            id={props.id}
            className={`qa-select${props.chico ? " qa-select-sm" : ""}`}
            aria-label={props.etiqueta}
            value={props.valor}
            disabled={props.disabled}
            onChange={(e) => props.onChange(e.target.value as Rol)}
        >
            {ROLES.map((r) => (
                <option key={r} value={r}>
                    {ROL_INFO[r].label}
                </option>
            ))}
        </select>
    );
}

export function Miembros({ yo }: { yo: string }) {
    const api = useApi();
    const toast = useToast();
    const [listado, setListado] = useState<Listado | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [nuevo, setNuevo] = useState({ github: "", rol: "qa" as Rol, invalido: false });

    const ejecutar = useCallback(
        async (accion: () => Promise<Listado>, ok?: string) => {
            try {
                setListado(await accion());
                setError(null);
                if (ok) toast(ok);
                return true;
            } catch (e) {
                setError((e as Error).message);
                return false;
            }
        },
        [toast],
    );

    useEffect(() => {
        void ejecutar(() => api<Listado>("/api/miembros"));
    }, [api, ejecutar]);

    async function agregar(ev: FormEvent) {
        ev.preventDefault();
        const github = nuevo.github.trim().replace(/^@/, "");
        if (!github) {
            setNuevo({ ...nuevo, invalido: true });
            setError("Escribí el usuario de GitHub.");
            return;
        }
        const ok = await ejecutar(
            () => api<Listado>("/api/miembros", { method: "POST", body: JSON.stringify({ github, rol: nuevo.rol }) }),
            `@${github} ya tiene acceso a la plataforma.`,
        );
        setNuevo({ ...nuevo, github: ok ? "" : nuevo.github, invalido: !ok });
    }

    const ruta = (m: Miembro) => `/api/miembros/${encodeURIComponent(m.github)}`;
    const cambiar = (m: Miembro, cambios: Partial<{ rol: Rol; habilitado: boolean }>, ok: string) =>
        ejecutar(() => api<Listado>(ruta(m), { method: "PATCH", body: JSON.stringify(cambios) }), ok);

    const quitar = (m: Miembro) => {
        if (!window.confirm(`@${m.github} ya no va a poder entrar a la plataforma de QA. ¿Quitar el acceso?`)) return;
        void ejecutar(() => api<Listado>(ruta(m), { method: "DELETE" }), `Se quitó el acceso de @${m.github}.`);
    };

    return (
        <section aria-labelledby="miembros">
            <div className="admin-section-head">
                <div>
                    <h2 className="admin-h2" id="miembros">
                        Miembros
                    </h2>
                    <p className="admin-muted admin-section-lead">
                        Quién puede entrar con su cuenta de GitHub. <strong>Solo lectura</strong> ve todo; <strong>QA</strong> además registra resultados y
                        publica; <strong>Admin</strong> además administra miembros. Los cambios aplican en el próximo pedido de cada persona.
                    </p>
                </div>
            </div>

            <form className="member-add" onSubmit={agregar} noValidate>
                <div className="form-group">
                    <div className="form-label-container">
                        <label className="form-label" htmlFor="member-username">
                            Usuario de GitHub
                        </label>
                    </div>
                    <div className="search-field">
                        <Icon name="alternate_email" />
                        <input
                            className={`form-control${nuevo.invalido ? " is-invalid" : ""}`}
                            id="member-username"
                            placeholder="usuario"
                            autoComplete="off"
                            spellCheck={false}
                            value={nuevo.github}
                            onChange={(e) => setNuevo({ ...nuevo, github: e.target.value, invalido: false })}
                        />
                    </div>
                </div>
                <div className="form-group">
                    <div className="form-label-container">
                        <label className="form-label" htmlFor="member-role">
                            Rol
                        </label>
                    </div>
                    <SelectorRol id="member-role" valor={nuevo.rol} onChange={(rol) => setNuevo({ ...nuevo, rol })} />
                </div>
                <button type="submit" className="btn btn-primary member-add-submit">
                    <Icon name="person_add" /> Dar acceso
                </button>
            </form>
            <div aria-live="polite">{error && <Alerta tipo="danger">{error}</Alerta>}</div>

            <div className="table-card">
                <table className="table table-borderless admin-table members-table">
                    <caption className="visually-hidden">Miembros de la plataforma de QA</caption>
                    <thead>
                        <tr>
                            <th scope="col">Usuario</th>
                            <th scope="col">Rol</th>
                            <th scope="col">Acceso</th>
                            <th scope="col">Agregado por</th>
                            <th scope="col" className="col-actions">
                                <span className="visually-hidden">Acciones</span>
                            </th>
                        </tr>
                    </thead>
                    <tbody>
                        {!listado && (
                            <tr>
                                <td colSpan={5} className="admin-muted">
                                    Cargando…
                                </td>
                            </tr>
                        )}
                        {listado?.fijos.map((f) => (
                            <tr key={f.github}>
                                <td>
                                    <Usuario github={f.github} esYo={f.github.toLowerCase() === yo.toLowerCase()} />
                                </td>
                                <td>
                                    <RoleBadge rol={f.rol} />
                                </td>
                                <td className="admin-muted">Fijo</td>
                                <td className="admin-muted">QA_ADMINS</td>
                                <td>
                                    <div className="row-actions">
                                        <span className="admin-muted small">Se cambia en wrangler.toml</span>
                                    </div>
                                </td>
                            </tr>
                        ))}
                        {listado?.miembros.map((m) => {
                            const esYo = m.github.toLowerCase() === yo.toLowerCase();
                            const acceso = `member-enabled-${m.github}`;
                            return (
                                <tr key={m.github} className={m.habilitado ? undefined : "is-disabled"}>
                                    <td>
                                        <Usuario github={m.github} esYo={esYo} />
                                    </td>
                                    <td>
                                        <SelectorRol
                                            id={`member-role-${m.github}`}
                                            etiqueta={`Rol de @${m.github}`}
                                            valor={m.rol}
                                            chico
                                            disabled={esYo}
                                            onChange={(rol) => void cambiar(m, { rol }, `@${m.github} ahora es ${ROL_INFO[rol].label}.`)}
                                        />
                                    </td>
                                    <td>
                                        <label className="switch" htmlFor={acceso}>
                                            <input
                                                type="checkbox"
                                                id={acceso}
                                                checked={m.habilitado}
                                                disabled={esYo}
                                                onChange={(e) =>
                                                    void cambiar(m, { habilitado: e.target.checked }, `@${m.github}: acceso ${e.target.checked ? "habilitado" : "suspendido"}.`)
                                                }
                                            />
                                            <span className="slider" />
                                            <span className="switch-label on">Activo</span>
                                            <span className="switch-label off">Suspendido</span>
                                            <span className="visually-hidden">Acceso de @{m.github}</span>
                                        </label>
                                    </td>
                                    <td className="admin-muted">{m.agregado_por ?? "—"}</td>
                                    <td>
                                        <div className="row-actions">
                                            {esYo ? (
                                                <span className="admin-muted small">Tu acceso lo cambia otro admin</span>
                                            ) : (
                                                <button
                                                    type="button"
                                                    className="btn btn-sm btn-outline-danger btn-icon-only"
                                                    title="Quitar acceso"
                                                    aria-label={`Quitar el acceso de @${m.github}`}
                                                    onClick={() => quitar(m)}
                                                >
                                                    <Icon name="person_remove" />
                                                </button>
                                            )}
                                        </div>
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </section>
    );
}

function Usuario({ github, esYo }: { github: string; esYo: boolean }) {
    return (
        <div className="member-cell">
            <img className="member-avatar" src={`https://github.com/${encodeURIComponent(github)}.png?size=72`} alt="" width={36} height={36} />
            <a className="cell-title" href={`https://github.com/${encodeURIComponent(github)}`} target="_blank" rel="noopener">
                @{github}
                {esYo && <span className="badge badge-s-info badge-sm me-badge">Vos</span>}
            </a>
        </div>
    );
}
