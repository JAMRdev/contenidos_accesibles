// Web de QA: login con Clerk (la misma instancia que /admin) y, con sesión, lo que permite el rol.
// Las pantallas de sesiones, casos y bugs llegan con #131.
import { Show, SignIn, SignOutButton, UserButton } from "@clerk/react";
import { useEffect, useState } from "react";
import { ApiError, ROL_TEXTO, useApi, type Yo } from "./api";
import { Estado } from "./Estado";
import { Miembros } from "./Miembros";

type Sesion = { kind: "cargando" } | { kind: "lista"; yo: Yo } | { kind: "sin-acceso" | "error"; mensaje: string };

function Inicio() {
    const api = useApi();
    const [sesion, setSesion] = useState<Sesion>({ kind: "cargando" });

    useEffect(() => {
        api<Yo>("/api/me")
            .then((yo) => setSesion({ kind: "lista", yo }))
            .catch((e: Error) =>
                setSesion({ kind: e instanceof ApiError && e.status === 403 ? "sin-acceso" : "error", mensaje: e.message }),
            );
    }, [api]);

    if (sesion.kind === "cargando") return <p aria-busy="true">Cargando tu acceso…</p>;
    if (sesion.kind !== "lista") {
        return (
            <section className="card" aria-labelledby="sin-acceso">
                <h2 id="sin-acceso">{sesion.kind === "sin-acceso" ? "Sin acceso" : "No se pudo entrar"}</h2>
                <p role="alert">{sesion.mensaje}</p>
                <SignOutButton>
                    <button type="button">Entrar con otra cuenta</button>
                </SignOutButton>
            </section>
        );
    }

    const { yo } = sesion;
    return (
        <>
            <p className="muted">
                Entraste como <strong>@{yo.github}</strong> · {ROL_TEXTO[yo.rol]}
            </p>
            <Estado />
            {yo.permisos.administrar && <Miembros yo={yo.github} />}
        </>
    );
}

export function App() {
    return (
        <main className="page">
            <header className="topbar">
                <div>
                    <h1>Plataforma de QA</h1>
                    <p className="muted">Backend de InnovaLab · contenidos accesibles</p>
                </div>
                <Show when="signed-in">
                    <UserButton />
                </Show>
            </header>

            <Show when="signed-out">
                <div className="login">
                    <SignIn routing="hash" />
                </div>
            </Show>
            <Show when="signed-in">
                <Inicio />
            </Show>
        </main>
    );
}
