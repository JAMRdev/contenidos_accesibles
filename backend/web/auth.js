// Login de la web de pruebas: el mismo acceso que el panel /admin (Clerk o el usuario del .env).
// El backend entrega un token de 8 h que se guarda en sessionStorage y viaja en cada llamada.
// Todo el contenido dinámico va por textContent: nada de innerHTML.
"use strict";

window.TestWebAuth = (() => {
    const STORAGE_KEY = "innovalab.testWebSession";
    const AUTH_PATH = "/api/v1/test-web/auth";
    const ROLE_LABEL = { admin: "Admin", viewer: "Solo lectura" };

    let apiBase = "";
    let config = null;
    let session = null;
    let onReady = () => {};
    let clerkPromise = null;
    let exchanging = false;

    const $ = (id) => document.getElementById(id);

    function storageRead() {
        try { return JSON.parse(sessionStorage.getItem(STORAGE_KEY)); } catch { return null; }
    }

    function storageWrite(value) {
        try {
            if (value) sessionStorage.setItem(STORAGE_KEY, JSON.stringify(value));
            else sessionStorage.removeItem(STORAGE_KEY);
        } catch { /* modo privado o storage bloqueado: la sesión dura lo que la pestaña */ }
    }

    async function call(method, path, body, withToken = false) {
        const headers = { Accept: "application/json" };
        if (body) headers["Content-Type"] = "application/json";
        if (withToken && session) headers.Authorization = `Bearer ${session.token}`;
        const res = await fetch(`${apiBase}${AUTH_PATH}${path}`, {
            method, headers, body: body ? JSON.stringify(body) : undefined,
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            const err = new Error(typeof data.detail === "string" ? data.detail : `HTTP ${res.status}`);
            err.status = res.status;
            throw err;
        }
        return data;
    }

    // -----------------------------------------------------------------------
    // Pantalla de login
    // -----------------------------------------------------------------------

    function setError(text) {
        const box = $("auth-error");
        $("auth-error-text").textContent = text || "";
        box.hidden = !text;
    }

    function setNotice(text) {
        const box = $("auth-notice");
        $("auth-notice-text").textContent = text || "";
        box.hidden = !text;
    }

    function lock() {
        document.body.classList.add("auth-locked");
        $("auth-screen").hidden = false;
        $("auth-backend").textContent = apiBase || "(sin configurar)";
        $("auth-user").hidden = true;
    }

    function unlock() {
        document.body.classList.remove("auth-locked");
        $("auth-screen").hidden = true;
        renderUser();
        onReady(session ? session.user : null);
    }

    function renderUser() {
        const bar = $("auth-user");
        if (!session) { bar.hidden = true; return; }
        const u = session.user;
        $("auth-user-name").textContent = u.kind === "github" ? `@${u.username}` : u.username;
        const role = $("auth-user-role");
        role.textContent = ROLE_LABEL[u.role] || u.role;
        role.className = `auth-role auth-role-${u.role}`;
        const avatar = $("auth-user-avatar");
        if (u.avatar_url) { avatar.src = u.avatar_url; avatar.hidden = false; } else { avatar.hidden = true; }
        bar.hidden = false;
    }

    function showLogin(notice) {
        lock();
        setError("");
        setNotice(notice || "");
        $("auth-local").hidden = !config.local;
        $("auth-clerk").hidden = !config.clerk;
        $("auth-fallback-summary").hidden = !(config.local && config.clerk);
        $("auth-fallback").classList.toggle("auth-fallback-only", Boolean(config.local && !config.clerk));
        $("auth-lead").textContent = config.clerk
            ? "Entrá con tu cuenta de GitHub o con el usuario de administración del backend."
            : "Ingresá con el usuario y la contraseña de administración del backend.";
        const fallback = $("auth-fallback");
        if (config.local && !config.clerk) fallback.open = true;
        if (config.clerk) mountClerk();
    }

    async function success(payload) {
        session = { ...payload, base: apiBase };
        storageWrite(session);
        setError("");
        unlock();
    }

    // -----------------------------------------------------------------------
    // Clerk (mismos scripts y misma aplicación que el panel)
    // -----------------------------------------------------------------------

    function loadScript(src, attrs = {}) {
        return new Promise((resolve, reject) => {
            const s = document.createElement("script");
            s.src = src;
            s.crossOrigin = "anonymous";
            for (const [k, v] of Object.entries(attrs)) s.setAttribute(k, v);
            s.onload = resolve;
            s.onerror = () => reject(new Error(`No se pudo cargar ${src}`));
            document.head.append(s);
        });
    }

    function loadClerk() {
        if (!clerkPromise) {
            const host = config.clerk.frontend_api;
            const pageUrl = `${location.origin}${location.pathname}`;
            clerkPromise = loadScript(`https://${host}/npm/@clerk/ui@1/dist/ui.browser.js`)
                .then(() => loadScript(`https://${host}/npm/@clerk/clerk-js@6/dist/clerk.browser.js`, {
                    "data-clerk-publishable-key": config.clerk.publishable_key,
                }))
                .then(() => window.Clerk.load({
                    ui: { ClerkUI: window.__internal_ClerkUICtor },
                    signInUrl: pageUrl, signUpUrl: pageUrl,
                    signInForceRedirectUrl: pageUrl, signUpForceRedirectUrl: pageUrl,
                    signInFallbackRedirectUrl: pageUrl, signUpFallbackRedirectUrl: pageUrl,
                    afterSignOutUrl: pageUrl,
                }))
                .then(() => {
                    window.Clerk.addListener(({ session: s }) => { if (s && document.body.classList.contains("auth-locked")) exchange(); });
                    return window.Clerk;
                });
            clerkPromise.catch(() => { clerkPromise = null; });
        }
        return clerkPromise;
    }

    async function mountClerk() {
        const slot = $("auth-clerk-slot");
        $("auth-clerk-loading").hidden = false;
        try {
            const clerk = await loadClerk();
            $("auth-clerk-loading").hidden = true;
            if (clerk.isSignedIn) { exchange(); return; }
            clerk.mountSignIn(slot, {
                forceRedirectUrl: `${location.origin}${location.pathname}`,
                appearance: {
                    variables: { colorPrimary: "#1d3343", colorText: "#08202f", fontFamily: "Archivo, system-ui, sans-serif", borderRadius: "12px" },
                    elements: { rootBox: "auth-clerk-root", cardBox: "auth-clerk-card", footerAction: { display: "none" } },
                },
            });
        } catch {
            $("auth-clerk-loading").textContent = "No se pudo cargar Clerk. Usá el acceso de respaldo.";
            $("auth-fallback").open = true;
        }
    }

    async function exchange() {
        if (exchanging || !window.Clerk || !window.Clerk.session) return;
        exchanging = true;
        try {
            const token = await window.Clerk.session.getToken();
            await success(await call("POST", "/clerk", { token }));
        } catch (err) {
            setError(err.message);
            $("auth-switch-account").hidden = false;
        } finally {
            exchanging = false;
        }
    }

    // -----------------------------------------------------------------------
    // API pública del módulo
    // -----------------------------------------------------------------------

    async function start(base, ready) {
        apiBase = base;
        if (ready) onReady = ready;
        session = null;
        if (!apiBase) { config = { required: false }; unlock(); return; }
        try {
            config = await call("GET", "/config");
        } catch (err) {
            // Backend sin login de la web de pruebas (versión anterior): se usa como siempre
            if (err.status === 404) { config = { required: false }; unlock(); return; }
            config = { required: true, clerk: null, local: true };
            lock();
            setError(`No se pudo conectar con el backend (${err.message}). Revisá la URL.`);
            return;
        }
        if (!config.required) { unlock(); return; }

        const saved = storageRead();
        if (saved && saved.base === apiBase && saved.token) {
            session = saved;
            try {
                session.user = (await call("GET", "/me", null, true)).user;
                storageWrite(session);
                unlock();
                return;
            } catch {
                session = null;
                storageWrite(null);
            }
        }
        showLogin();
    }

    function token() {
        return session ? session.token : null;
    }

    function expired() {
        session = null;
        storageWrite(null);
        if (config) showLogin("Tu sesión venció o se revocó el acceso. Volvé a iniciar sesión.");
    }

    async function logout() {
        session = null;
        storageWrite(null);
        if (window.Clerk && window.Clerk.isSignedIn) await window.Clerk.signOut();
        showLogin("Cerraste la sesión.");
    }

    function init() {
        $("auth-local-form").addEventListener("submit", async (ev) => {
            ev.preventDefault();
            setError("");
            const button = $("auth-local-submit");
            button.disabled = true;
            try {
                await success(await call("POST", "/login", {
                    username: $("auth-username").value.trim(),
                    password: $("auth-password").value,
                }));
                $("auth-password").value = "";
            } catch (err) {
                setError(err.message);
            } finally {
                button.disabled = false;
            }
        });
        $("auth-password-toggle").addEventListener("click", () => {
            const input = $("auth-password");
            const show = input.type === "password";
            input.type = show ? "text" : "password";
            const btn = $("auth-password-toggle");
            btn.setAttribute("aria-pressed", String(show));
            btn.setAttribute("aria-label", show ? "Ocultar contraseña" : "Mostrar contraseña");
            btn.querySelector(".material-symbols-rounded").textContent = show ? "visibility_off" : "visibility";
        });
        $("auth-switch-account").addEventListener("click", async () => {
            $("auth-switch-account").hidden = true;
            setError("");
            if (window.Clerk) await window.Clerk.signOut();
            mountClerk();
        });
        $("auth-logout").addEventListener("click", logout);
        $("auth-change-backend").addEventListener("click", () => {
            $("auth-backend-form").hidden = false;
            $("auth-backend-input").value = apiBase;
            $("auth-backend-input").focus();
        });
        $("auth-backend-form").addEventListener("submit", (ev) => {
            ev.preventDefault();
            $("auth-backend-form").hidden = true;
            document.dispatchEvent(new CustomEvent("testweb:change-backend", { detail: $("auth-backend-input").value }));
        });
    }

    document.addEventListener("DOMContentLoaded", init);
    return { start, token, expired, logout };
})();
