import { ClerkProvider } from "@clerk/react";
import { esUY } from "@clerk/localizations";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";

const root = createRoot(document.getElementById("root")!);

function Fallo({ mensaje }: { mensaje: string }) {
    return (
        <main className="admin-container admin-main">
            <h1 className="admin-h1">Plataforma de QA</h1>
            <div className="alert alert-danger mt-3" role="alert">
                <p className="mb-0">{mensaje}</p>
            </div>
        </main>
    );
}

// La publishable key de Clerk sale del Worker (wrangler.toml), así no hay que repetirla en el build
fetch("/api/config")
    .then((res) => (res.ok ? (res.json() as Promise<{ clerk_publishable_key: string }>) : Promise.reject()))
    .then(({ clerk_publishable_key }) =>
        root.render(
            <StrictMode>
                <ClerkProvider publishableKey={clerk_publishable_key} localization={esUY}>
                    <App />
                </ClerkProvider>
            </StrictMode>,
        ),
    )
    .catch(() => root.render(<Fallo mensaje="No se pudo cargar la configuración de la plataforma. Recargá la página." />));
