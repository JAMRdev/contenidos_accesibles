// Web de QA: login con Clerk (la misma instancia que /admin) y, con sesión, lo que permite el rol.
// Mismo diseño que el panel /admin (Obelisco V2).
import { ClerkLoaded, ClerkLoading, Show, SignIn, useClerk } from "@clerk/react";
import { useEffect, useState, type ReactNode } from "react";
import { ApiError, useApi, YoContext, type Yo } from "./api";
import { Encabezado } from "./Encabezado";
import { Miembros } from "./Miembros";
import { BugDetallePagina, Bugs } from "./paginas/Bugs";
import { CasoDetalle, Casos } from "./paginas/Casos";
import { EvidenciaPagina } from "./paginas/Evidencia";
import { Inicio } from "./paginas/Inicio";
import { SesionDetalle } from "./paginas/SesionDetalle";
import { Sesiones } from "./paginas/Sesiones";
import { Link, RutaProvider, useRuta } from "./ruta";
import { Alerta, Icon, Marca, ToastProvider } from "./ui";

// Misma apariencia que el login de /admin (backend/app/admin/static/admin.js)
const APARIENCIA_CLERK = {
    variables: { colorPrimary: "#1d3343", colorText: "#08202f", fontFamily: "Archivo, system-ui, sans-serif", borderRadius: "12px" },
    // Sin enlace de registro: el acceso lo dan los admins
    elements: { rootBox: "clerk-root", cardBox: "clerk-card", footerAction: { display: "none" } },
};

function PaginaLogin({ titulo, bajada, children }: { titulo: string; bajada: string; children: ReactNode }) {
    return (
        <div className="admin-login-page">
            <main id="main" className="admin-container admin-main">
                <div className="login-wrap">
                    <div className="login-brand">
                        <Marca />
                    </div>
                    <div className="login-card">
                        <h1 className="login-title">{titulo}</h1>
                        <p className="login-lead">{bajada}</p>
                        {children}
                    </div>
                </div>
            </main>
        </div>
    );
}

function Login() {
    return (
        <PaginaLogin titulo="Iniciar sesión" bajada="Entrá con tu cuenta de GitHub, la misma del panel de administración.">
            <div className="clerk-slot" aria-live="polite">
                <ClerkLoading>
                    <div className="clerk-loading">
                        <span className="spinner-border spinner-border-sm" aria-hidden="true" /> Cargando opciones de inicio de sesión…
                    </div>
                </ClerkLoading>
                <ClerkLoaded>
                    <SignIn routing="hash" appearance={APARIENCIA_CLERK} />
                </ClerkLoaded>
            </div>
            <p className="login-note">
                <Icon name="lock" />
                El acceso lo da un admin de la plataforma desde la sección Miembros.
            </p>
        </PaginaLogin>
    );
}

function SinAcceso({ titulo, mensaje }: { titulo: string; mensaje: string }) {
    const { signOut } = useClerk();
    return (
        <PaginaLogin titulo={titulo} bajada="No se pudo abrir la plataforma con esta cuenta.">
            <div className="alert alert-danger" role="alert">
                <p className="mb-0">{mensaje}</p>
                <button type="button" className="btn btn-sm btn-outline-danger mt-2" onClick={() => void signOut()}>
                    Usar otra cuenta
                </button>
            </div>
        </PaginaLogin>
    );
}

// Qué página muestra cada ruta (la navegación está en Encabezado)
function Pagina({ yo }: { yo: Yo }) {
    const { path } = useRuta();
    const [, raiz, id] = path.split("/");
    if (raiz === "" || raiz === undefined) return <Inicio />;
    if (raiz === "sesiones") return id ? <SesionDetalle key={id} id={id} /> : <Sesiones />;
    if (raiz === "bugs") return id ? <BugDetallePagina key={id} id={id} /> : <Bugs />;
    if (raiz === "casos") return id ? <CasoDetalle key={id} id={decodeURIComponent(id)} /> : <Casos />;
    if (raiz === "evidencias" && id) return <EvidenciaPagina key={id} id={id} />;
    if (raiz === "miembros" && yo.permisos.administrar) return <Miembros yo={yo.github} />;
    return (
        <Alerta tipo="warning">
            No existe esta página. <Link href="/">Volver al inicio</Link>.
        </Alerta>
    );
}

type Sesion = { kind: "cargando" } | { kind: "lista"; yo: Yo } | { kind: "sin-acceso" | "error"; mensaje: string };

function Plataforma() {
    const api = useApi();
    const [sesion, setSesion] = useState<Sesion>({ kind: "cargando" });

    useEffect(() => {
        api<Yo>("/api/me")
            .then((yo) => setSesion({ kind: "lista", yo }))
            .catch((e: Error) => setSesion({ kind: e instanceof ApiError && e.status === 403 ? "sin-acceso" : "error", mensaje: e.message }));
    }, [api]);

    if (sesion.kind === "sin-acceso") return <SinAcceso titulo="Sin acceso" mensaje={sesion.mensaje} />;
    if (sesion.kind === "error") return <SinAcceso titulo="No se pudo entrar" mensaje={sesion.mensaje} />;

    const yo = sesion.kind === "lista" ? sesion.yo : null;
    return (
        <>
            <a href="#main" className="skip-to-main-content-link">
                Saltar al contenido principal
            </a>
            <Encabezado yo={yo} />
            <main id="main" className="admin-container admin-main">
                {!yo ? (
                    <p className="admin-muted" aria-busy="true">
                        Cargando tu acceso…
                    </p>
                ) : (
                    <YoContext.Provider value={yo}>
                        <Pagina yo={yo} />
                    </YoContext.Provider>
                )}
            </main>
        </>
    );
}

export function App() {
    return (
        <ToastProvider>
            <RutaProvider>
                <Show when="signed-out">
                    <Login />
                </Show>
                <Show when="signed-in">
                    <Plataforma />
                </Show>
            </RutaProvider>
        </ToastProvider>
    );
}
