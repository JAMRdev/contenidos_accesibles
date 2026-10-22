// Encabezado con la marca, la navegación y el menú de usuario, igual que el del panel /admin.
import { useClerk } from "@clerk/react";
import { useEffect, useRef, useState } from "react";
import type { Yo } from "./api";
import { Icon, Marca, ROL_INFO } from "./ui";

const ENLACES = [
    { href: "https://github.com/e18-innovalab/contenidos_accesibles/blob/main/tester/GUIA_QA_BACKEND.md", icon: "menu_book", texto: "Guía de QA" },
    { href: "https://innovalab-educacion-e18-test.up.railway.app/docs", icon: "api", texto: "Swagger" },
    { href: "https://innovalab-web-pruebas.onrender.com/", icon: "science", texto: "Web de pruebas" },
    { href: "https://innovalab-educacion-e18-test.up.railway.app/admin", icon: "admin_panel_settings", texto: "Panel" },
];

function MenuUsuario({ yo }: { yo: Yo }) {
    const { signOut } = useClerk();
    const [abierto, setAbierto] = useState(false);
    const caja = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!abierto) return;
        const cerrar = (ev: MouseEvent | KeyboardEvent) => {
            if (ev instanceof KeyboardEvent ? ev.key === "Escape" : !caja.current?.contains(ev.target as Node)) setAbierto(false);
        };
        document.addEventListener("mousedown", cerrar);
        document.addEventListener("keydown", cerrar);
        return () => {
            document.removeEventListener("mousedown", cerrar);
            document.removeEventListener("keydown", cerrar);
        };
    }, [abierto]);

    const rol = ROL_INFO[yo.rol];
    return (
        <div className="dropdown admin-user-menu" ref={caja}>
            <button type="button" className="admin-nav-link admin-user-button" aria-expanded={abierto} onClick={() => setAbierto(!abierto)}>
                {yo.avatar_url ? <img className="admin-avatar" src={yo.avatar_url} alt="" width={28} height={28} /> : <Icon name="account_circle" />}
                <span>{yo.github}</span>
                <span className="material-symbols-rounded admin-caret" aria-hidden="true">
                    expand_more
                </span>
            </button>
            <div className={`dropdown-menu dropdown-menu-end admin-user-dropdown${abierto ? " show" : ""}`}>
                <div className="admin-user-info">
                    <span className="admin-user-name">{yo.nombre ?? yo.github}</span>
                    <span className="admin-user-env">
                        <Icon name="code" />@{yo.github} en GitHub
                    </span>
                    <span className={`role-badge role-${yo.rol}`}>
                        <Icon name={rol.icon} />
                        {rol.label}
                    </span>
                </div>
                <button type="button" className="dropdown-item admin-logout" onClick={() => void signOut()}>
                    <Icon name="logout" />
                    <span className="item-text">Cerrar sesión</span>
                </button>
            </div>
        </div>
    );
}

export function Encabezado({ yo }: { yo: Yo | null }) {
    return (
        <header className="admin-header" role="banner">
            <div className="admin-container admin-header-inner">
                <a href="/" className="admin-brand">
                    <Marca />
                </a>
                <nav className="admin-nav" aria-label="Navegación de la plataforma">
                    <ul>
                        <li>
                            <a className="admin-nav-link is-active" href="/" aria-current="page">
                                <Icon name="space_dashboard" />
                                Inicio
                            </a>
                        </li>
                        {ENLACES.map((e) => (
                            <li key={e.href}>
                                <a className="admin-nav-link" href={e.href} target="_blank" rel="noopener">
                                    <Icon name={e.icon} />
                                    {e.texto}
                                    <span className="visually-hidden">(se abre en otra pestaña)</span>
                                </a>
                            </li>
                        ))}
                    </ul>
                </nav>
                {yo && <MenuUsuario yo={yo} />}
            </div>
        </header>
    );
}
