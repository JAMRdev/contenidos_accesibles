// Encabezado con la marca, la navegación y el menú de usuario, igual que el del panel /admin.
import { useClerk } from "@clerk/react";
import { useEffect, useRef, useState } from "react";
import type { Yo } from "./api";
import { Link, useRuta } from "./ruta";
import { Icon, Marca, ROL_INFO } from "./ui";

// Secciones de la web (las rutas las resuelve App)
const SECCIONES = [
    { href: "/", icon: "space_dashboard", texto: "Inicio" },
    { href: "/sesiones", icon: "playlist_add_check", texto: "Sesiones" },
    { href: "/bugs", icon: "bug_report", texto: "Bugs" },
    { href: "/casos", icon: "fact_check", texto: "Catálogo" },
    { href: "/miembros", icon: "group", texto: "Miembros", soloAdmin: true },
];

// Herramientas del equipo, en el menú de usuario
const ENLACES = [
    { href: "https://github.com/e18-innovalab/contenidos_accesibles/blob/main/tester/GUIA_QA_BACKEND.md", icon: "menu_book", texto: "Guía de QA" },
    { href: "https://innovalab-educacion-e18-test.up.railway.app/docs", icon: "api", texto: "Swagger" },
    { href: "https://innovalab-web-pruebas.onrender.com/", icon: "science", texto: "Web de pruebas" },
    { href: "https://innovalab-educacion-e18-test.up.railway.app/admin", icon: "admin_panel_settings", texto: "Panel de administración" },
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
                {ENLACES.map((e) => (
                    <a key={e.href} className="dropdown-item admin-menu-link" href={e.href} target="_blank" rel="noopener">
                        <Icon name={e.icon} />
                        <span className="item-text">{e.texto}</span>
                        <span className="visually-hidden">(se abre en otra pestaña)</span>
                    </a>
                ))}
                <button type="button" className="dropdown-item admin-logout" onClick={() => void signOut()}>
                    <Icon name="logout" />
                    <span className="item-text">Cerrar sesión</span>
                </button>
            </div>
        </div>
    );
}

export function Encabezado({ yo }: { yo: Yo | null }) {
    const { path } = useRuta();
    const activa = (href: string) => (href === "/" ? path === "/" : path === href || path.startsWith(`${href}/`));
    return (
        <header className="admin-header" role="banner">
            <div className="admin-container admin-header-inner">
                <Link href="/" className="admin-brand">
                    <Marca />
                </Link>
                <nav className="admin-nav" aria-label="Navegación de la plataforma">
                    <ul>
                        {SECCIONES.filter((s) => !s.soloAdmin || yo?.permisos.administrar).map((s) => (
                            <li key={s.href}>
                                <Link className={`admin-nav-link${activa(s.href) ? " is-active" : ""}`} href={s.href} aria-current={activa(s.href) ? "page" : undefined}>
                                    <Icon name={s.icon} />
                                    {s.texto}
                                </Link>
                            </li>
                        ))}
                    </ul>
                </nav>
                {yo && <MenuUsuario yo={yo} />}
            </div>
        </header>
    );
}
