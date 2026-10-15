// Panel /adminv2: métricas, conexiones y gráficos SVG sin dependencias.
// Todo el contenido dinámico se arma con nodos y textContent (nunca innerHTML).
"use strict";

// Mostrar u ocultar contraseñas (login y modal de conexión)
document.querySelectorAll("[data-toggle-password]").forEach((btn) => {
    btn.addEventListener("click", () => {
        const input = document.getElementById(btn.dataset.togglePassword);
        const show = input.type === "password";
        input.type = show ? "text" : "password";
        btn.setAttribute("aria-pressed", show ? "true" : "false");
        btn.setAttribute("aria-label", btn.getAttribute("aria-label").replace(show ? "Mostrar" : "Ocultar", show ? "Ocultar" : "Mostrar"));
        btn.querySelector(".material-symbols-rounded").textContent = show ? "visibility_off" : "visibility";
    });
});

// Desplegable de selección de Obelisco (.o-select): el valor vive en un input oculto
const ObSelect = {
    init(root) {
        const hidden = root.querySelector('input[type="hidden"]');
        root.querySelectorAll("[role=option]").forEach((opt) => opt.addEventListener("click", () => {
            ObSelect.set(hidden.id, opt.dataset.value);
            hidden.dispatchEvent(new Event("change", { bubbles: true }));
            root.querySelector(".o-select-button").focus();
        }));
        ObSelect.set(hidden.id, hidden.value);
    },
    set(id, value) {
        const hidden = document.getElementById(id);
        const root = hidden.closest(".o-select");
        let match = root.querySelector(`[role=option][data-value="${CSS.escape(value)}"]`);
        if (!match && value) {
            // Valor que no está en la lista (p. ej. un proveedor cargado a mano): se agrega como opción
            const li = document.createElement("li");
            li.setAttribute("role", "presentation");
            match = document.createElement("button");
            Object.assign(match, { type: "button", className: "dropdown-item" });
            match.setAttribute("role", "option");
            match.dataset.value = value;
            const text = document.createElement("span");
            text.className = "item-text";
            text.textContent = value;
            match.append(text);
            li.append(match);
            root.querySelector("[role=listbox]").append(li);
            match.addEventListener("click", () => { ObSelect.set(id, value); hidden.dispatchEvent(new Event("change", { bubbles: true })); });
        }
        match = match || root.querySelector("[role=option]");
        hidden.value = match ? match.dataset.value : "";
        root.querySelectorAll("[role=option]").forEach((opt) => {
            const on = opt === match;
            opt.classList.toggle("active", on);
            opt.setAttribute("aria-selected", on ? "true" : "false");
        });
        root.querySelector("[data-select-text]").textContent = match ? match.querySelector(".item-text").textContent : "";
    },
};
document.querySelectorAll(".o-select").forEach((root) => ObSelect.init(root));

// Login con Clerk: monta <SignIn> y, con la sesión de Clerk activa, la canjea por la cookie del panel
(() => {
    const configNode = document.getElementById("clerk-config");
    if (!configNode) return;
    const cfg = JSON.parse(configNode.textContent);
    const slot = document.getElementById("clerk-sign-in");
    const errorBox = document.getElementById("clerk-error");
    let exchanging = false;
    // Toda redirección de Clerk (después de entrar, de registrarse o de volver de GitHub) vuelve al
    // login del panel; ahí se canjea la sesión y se entra a `next`. Nunca a la raíz de la API.
    const loginUrl = `${location.origin}${cfg.prefix}/login?next=${encodeURIComponent(cfg.next || cfg.prefix)}`;
    const redirects = {
        signInUrl: loginUrl,
        signUpUrl: loginUrl,
        signInForceRedirectUrl: loginUrl,
        signUpForceRedirectUrl: loginUrl,
        signInFallbackRedirectUrl: loginUrl,
        signUpFallbackRedirectUrl: loginUrl,
        afterSignOutUrl: `${location.origin}${cfg.prefix}/login?bye=1`,
    };

    const showError = (text) => {
        document.getElementById("clerk-error-text").textContent = text;
        errorBox.hidden = false;
    };

    async function exchange() {
        if (exchanging || !window.Clerk.session) return;
        exchanging = true;
        errorBox.hidden = true;
        try {
            const token = await window.Clerk.session.getToken();
            const r = await fetch(`${location.origin}${cfg.prefix}/auth/clerk`, {
                method: "POST",
                headers: { "Content-Type": "application/json", Accept: "application/json" },
                body: JSON.stringify({ token, next: cfg.next }),
            });
            const data = await r.json().catch(() => ({}));
            if (r.ok) {
                location.assign(data.redirect || cfg.prefix);
                return;
            }
            showError(data.detail || `No se pudo iniciar sesión (HTTP ${r.status}).`);
        } catch (err) {
            showError(`No se pudo iniciar sesión (${err.message}).`);
        }
        exchanging = false;
    }

    document.getElementById("clerk-switch-account").addEventListener("click", async () => {
        errorBox.hidden = true;
        await window.Clerk.signOut();
        mount();
    });

    function mount() {
        window.Clerk.mountSignIn(slot, {
            forceRedirectUrl: loginUrl,
            fallbackRedirectUrl: loginUrl,
            signUpForceRedirectUrl: loginUrl,
            signUpFallbackRedirectUrl: loginUrl,
            signUpUrl: loginUrl,
            appearance: {
                variables: {
                    colorPrimary: "#1d3343",
                    colorText: "#08202f",
                    fontFamily: "Archivo, system-ui, sans-serif",
                    borderRadius: "12px",
                },
                // Sin enlace "Sign up": el acceso lo dan los admins y la instancia de desarrollo
                // de Clerk tiene un tope de 100 usuarios
                elements: { rootBox: "clerk-root", cardBox: "clerk-card", footerAction: { display: "none" } },
            },
        });
    }

    window.addEventListener("load", async () => {
        try {
            await window.Clerk.load({ ui: { ClerkUI: window.__internal_ClerkUICtor }, ...redirects });
        } catch (err) {
            document.getElementById("clerk-loading").textContent = "No se pudo cargar Clerk. Usá el acceso de respaldo.";
            return;
        }
        document.getElementById("clerk-loading")?.remove();
        // Al cerrar sesión en el panel también se cierra la de Clerk
        if (cfg.bye && window.Clerk.isSignedIn) await window.Clerk.signOut();
        window.Clerk.addListener(({ session }) => { if (session) exchange(); });
        if (window.Clerk.isSignedIn) exchange();
        else mount();
    });
})();

(() => {
    const configNode = document.getElementById("admin-config");
    if (!configNode) return;
    const CONFIG = JSON.parse(configNode.textContent);
    const PREFIX = CONFIG.prefix;
    // URL absoluta sin usuario ni contraseña: fetch rechaza las rutas relativas si la página
    // se abrió como http://usuario:clave@host/... (el navegador reutiliza la sesión de Basic Auth igual)
    const api = (path) => `${location.origin}${PREFIX}${path}`;
    const SVG_NS = "http://www.w3.org/2000/svg";
    const REFRESH_MS = 30000;

    const css = getComputedStyle(document.documentElement);
    const token = (name) => css.getPropertyValue(name).trim();
    const C = {
        s1: token("--series-1"), s2: token("--series-2"), s3: token("--series-3"), s4: token("--series-4"),
        neutral: token("--neutral"), good: token("--status-good"), warning: token("--status-warning"),
        critical: token("--status-critical"), surface: token("--surface"), ink: token("--ink"),
    };
    const SERIES = [C.s1, C.s2, C.s3, C.s4];

    const LABELS = {
        category: { comprension: "Comprensión", estructura: "Estructura", accesibilidad_visual: "Accesibilidad visual" },
        severity: { para_revisar: "Para revisar", barrera_detectada: "Barrera detectada" },
        adaptation: {
            lenguaje_claro: "Lenguaje claro", consigna_en_pasos: "Consigna en pasos",
            reorganizar: "Reorganizar", mejorar_estructura: "Mejorar estructura",
        },
        decision: { aceptada: "Aceptada", editada: "Editada", descartada: "Descartada", pendiente: "Pendiente" },
        source: { text: "Texto", pdf: "PDF", docx: "Word", pptx: "PowerPoint" },
    };
    const STATUS = {
        active: { icon: "check_circle", label: "Activa" },
        standby: { icon: "pause_circle", label: "En espera" },
        "near-limit": { icon: "warning", label: "Cerca del límite" },
        exhausted: { icon: "block", label: "Agotada" },
        disabled: { icon: "do_not_disturb_on", label: "Deshabilitada" },
    };
    const PROVIDER_LABELS = Object.fromEntries((CONFIG.presets || []).map((p) => [p.id, p.label]));
    const providerLabel = (id) => (id === "otros" ? "Otros" : (PROVIDER_LABELS[id] || id).replace(/\s*\(.*\)$/, ""));

    const state = { range: "24h", data: null, timer: null };
    const ME = CONFIG.me || { can_edit: false };
    const CAN_EDIT = Boolean(ME.can_edit);
    const $ = (id) => document.getElementById(id);

    // -----------------------------------------------------------------------
    // DOM
    // -----------------------------------------------------------------------

    function build(ns, tag, attrs, children) {
        const node = ns ? document.createElementNS(ns, tag) : document.createElement(tag);
        for (const [k, v] of Object.entries(attrs || {})) {
            if (v === null || v === undefined || v === false) continue;
            if (k === "class") node.setAttribute("class", v);
            else if (k === "text") node.textContent = v;
            else if (k === "style" && typeof v === "object") Object.assign(node.style, v);
            else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
            else node.setAttribute(k, v === true ? "" : v);
        }
        for (const child of children.flat(Infinity)) {
            if (child === null || child === undefined || child === false) continue;
            node.append(child instanceof Node ? child : document.createTextNode(String(child)));
        }
        return node;
    }
    const h = (tag, attrs, ...children) => build(null, tag, attrs, children);
    const s = (tag, attrs, ...children) => build(SVG_NS, tag, attrs, children);
    const icon = (name) => h("span", { class: "material-symbols-rounded", "aria-hidden": "true", text: name });

    // -----------------------------------------------------------------------
    // Formatos
    // -----------------------------------------------------------------------

    const nf = new Intl.NumberFormat("es-AR");
    const compactNf = new Intl.NumberFormat("es-AR", { notation: "compact", maximumFractionDigits: 1 });
    const num = (v) => (v === null || v === undefined ? "—" : nf.format(v));
    const compact = (v) => (v === null || v === undefined ? "—" : v >= 10000 ? compactNf.format(v) : nf.format(v));
    const ms = (v) => (v === null || v === undefined ? "—" : v >= 1000 ? `${nf.format(Math.round(v / 100) / 10)} s` : `${nf.format(v)} ms`);
    const pct = (v) => (v === null || v === undefined ? "—" : `${nf.format(v)} %`);
    const hourFmt = new Intl.DateTimeFormat("es-AR", { hour: "2-digit", minute: "2-digit", hour12: false });
    const dayFmt = new Intl.DateTimeFormat("es-AR", { day: "numeric", month: "short" });
    const fullFmt = new Intl.DateTimeFormat("es-AR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });

    function bucketLabel(iso, unit, long = false) {
        const d = new Date(iso);
        if (unit === "hour") return long ? `${dayFmt.format(d)}, ${hourFmt.format(d)} h` : `${hourFmt.format(d)}`;
        return dayFmt.format(d).replace(".", "");
    }

    // Escala "linda": 0 a un máximo redondeado con 3-5 marcas (pasos 1, 2 o 5 × 10^k)
    function niceScale(max, ticks = 4) {
        if (!max || max <= 0) return { max: 1, step: 1, ticks: [0, 1] };
        const raw = max / ticks;
        const mag = 10 ** Math.floor(Math.log10(raw));
        const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((st) => st >= raw);
        const top = Math.ceil(max / step) * step;
        const list = [];
        for (let v = 0; v <= top + step / 2; v += step) list.push(Math.round(v * 1000) / 1000);
        return { max: top, step, ticks: list };
    }

    // -----------------------------------------------------------------------
    // Tooltip
    // -----------------------------------------------------------------------

    const tooltip = $("chart-tooltip");

    function showTooltip(title, rows, x, y) {
        tooltip.replaceChildren(
            h("strong", { text: title }),
            ...rows.map((r) => h("div", { class: "tt-row" },
                h("span", { class: "tt-key" },
                    r.color ? h("span", { class: "tt-swatch", style: { background: r.color } }) : null,
                    r.label),
                h("span", { class: "tt-value", text: r.value }))),
        );
        tooltip.hidden = false;
        const pad = 14;
        const { width, height } = tooltip.getBoundingClientRect();
        let left = x + pad;
        let top = y - height - pad;
        if (left + width > window.innerWidth - 8) left = x - width - pad;
        if (top < 8) top = y + pad;
        tooltip.style.left = `${Math.max(8, left)}px`;
        tooltip.style.top = `${top}px`;
    }
    const hideTooltip = () => { tooltip.hidden = true; };

    function anchorOf(node) {
        const r = node.getBoundingClientRect();
        return [r.left + r.width / 2, r.top];
    }

    // -----------------------------------------------------------------------
    // Tarjeta de gráfico: título, leyenda, SVG y vista de tabla
    // -----------------------------------------------------------------------

    function chartCard(container, { title, subtitle, legend, svg, table, empty }) {
        const head = h("div", { class: "chart-head" },
            h("div", {}, h("h3", { class: "chart-title", text: title }),
                subtitle ? h("p", { class: "chart-subtitle", text: subtitle }) : null));
        if (empty) {
            container.replaceChildren(head, h("div", { class: "chart-empty" }, icon(empty.icon || "monitoring"),
                h("span", { text: empty.text })));
            return;
        }
        const legendNode = legend && legend.length > 1
            ? h("ul", { class: "chart-legend", "aria-label": "Leyenda" }, legend.map((l) => h("li", {},
                h("span", { class: `legend-swatch${l.line ? " line" : ""}`, style: { background: l.color } }), l.label)))
            : null;
        const tableNode = table ? h("details", { class: "chart-table-toggle" },
            h("summary", { text: "Ver como tabla" }),
            h("div", { class: "responsive-scroll", tabindex: "0" },
                h("table", { class: "table table-borderless" },
                    h("caption", { class: "visually-hidden", text: title }),
                    h("thead", {}, h("tr", {}, table.headers.map((t, i) => h("th", { scope: "col", class: i ? "num" : null, text: t })))),
                    h("tbody", {}, table.rows.map((row) => h("tr", {},
                        row.map((cell, i) => (i ? h("td", { class: "num", text: cell }) : h("th", { scope: "row", text: cell }))))))))) : null;
        container.replaceChildren(...[head, legendNode, svg, tableNode].filter(Boolean));
    }

    // Ancho útil de la tarjeta (sin padding). En una pestaña oculta mide 0: se redibuja al mostrarla.
    function widthOf(id) {
        const node = $(id);
        const w = node.clientWidth - 40;
        return w > 200 ? Math.round(w) : null;
    }

    // Recorta un texto del SVG al ancho disponible (aprox. 6,6 px por carácter a 12 px) y deja el completo en <title>
    function fitText(attrs, text, maxWidth) {
        const max = Math.max(4, Math.floor(maxWidth / 6.6));
        const short = text.length > max ? `${text.slice(0, max - 1)}…` : text;
        const node = s("text", { ...attrs, text: short });
        if (short !== text) node.append(s("title", { text }));
        return node;
    }

    function svgRoot(width, height, label) {
        return s("svg", { class: "chart-svg", viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": label });
    }

    function yAxis(svg, scale, y, x0, x1, fmt) {
        for (const t of scale.ticks) {
            const yy = y(t);
            svg.append(s("line", { class: t === 0 ? "axis-line" : "grid-line", x1: x0, x2: x1, y1: yy, y2: yy }));
            svg.append(s("text", { x: x0 - 8, y: yy + 4, "text-anchor": "end", text: fmt(t) }));
        }
    }

    function xLabels(svg, labels, xOf, y, maxLabels) {
        const every = Math.max(1, Math.ceil(labels.length / maxLabels));
        labels.forEach((label, i) => {
            if (i % every !== 0 && i !== labels.length - 1) return;
            if (i !== labels.length - 1 && labels.length - 1 - i < every) return;
            svg.append(s("text", { x: xOf(i), y, "text-anchor": "middle", text: label }));
        });
    }

    // Path de una barra con las esquinas de la punta redondeadas (r = 4)
    function barPath(x, y, w, hgt, r, horizontal = false) {
        r = Math.min(r, horizontal ? hgt / 2 : w / 2, horizontal ? w : hgt);
        if (r <= 0 || w <= 0 || hgt <= 0) return `M${x},${y}h${Math.max(w, 0)}v${Math.max(hgt, 0)}h${-Math.max(w, 0)}Z`;
        if (horizontal) {
            return `M${x},${y}h${w - r}a${r},${r} 0 0 1 ${r},${r}v${hgt - 2 * r}a${r},${r} 0 0 1 ${-r},${r}h${-(w - r)}Z`;
        }
        return `M${x},${y + hgt}v${-(hgt - r)}a${r},${r} 0 0 1 ${r},${-r}h${w - 2 * r}a${r},${r} 0 0 1 ${r},${r}v${hgt - r}Z`;
    }

    // -----------------------------------------------------------------------
    // Gráfico de líneas con cruz y tooltip
    // -----------------------------------------------------------------------

    function lineChart({ labels, longLabels, series, fmt, ariaLabel, width }) {
        const W = width || 960, H = 300, m = { l: 64, r: 130, t: 16, b: 32 };
        const iw = W - m.l - m.r, ih = H - m.t - m.b;
        const max = Math.max(0, ...series.flatMap((sr) => sr.values));
        const scale = niceScale(max);
        const x = (i) => m.l + (labels.length === 1 ? iw / 2 : (i * iw) / (labels.length - 1));
        const y = (v) => m.t + ih - (v / scale.max) * ih;
        const svg = svgRoot(W, H, ariaLabel);

        yAxis(svg, scale, y, m.l, m.l + iw, compact);
        xLabels(svg, labels, x, H - 8, 8);

        series.forEach((sr) => {
            const d = sr.values.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join("");
            svg.append(s("path", { d, fill: "none", stroke: sr.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
            const last = sr.values.length - 1;
            svg.append(s("circle", { cx: x(last), cy: y(sr.values[last]), r: 4, fill: sr.color, stroke: C.surface, "stroke-width": 2 }));
        });

        // Etiquetas directas al final de cada línea, separadas para que no se pisen
        if (series.length <= 4) {
            const ends = series.map((sr) => ({ sr, y: y(sr.values[sr.values.length - 1]) })).sort((a, b) => a.y - b.y);
            for (let i = 1; i < ends.length; i++) ends[i].y = Math.max(ends[i].y, ends[i - 1].y + 16);
            const overflow = ends.length ? ends[ends.length - 1].y - (m.t + ih) : 0;
            if (overflow > 0) ends.forEach((e) => { e.y -= overflow; });
            for (const e of ends) {
                svg.append(s("text", { class: "value-label", x: m.l + iw + 10, y: e.y + 4, text: e.sr.label }));
            }
        }

        const hover = s("line", { class: "hover-line", y1: m.t, y2: m.t + ih, visibility: "hidden" });
        const hoverDots = series.map((sr) => s("circle", { r: 5, fill: sr.color, stroke: C.surface, "stroke-width": 2, visibility: "hidden" }));
        svg.append(hover, ...hoverDots);

        const overlay = s("rect", { class: "hit", x: m.l, y: m.t, width: iw, height: ih, tabindex: "0", "aria-label": "Recorrer los valores con las flechas" });
        let current = labels.length - 1;
        const show = (i, cx, cy) => {
            current = Math.max(0, Math.min(labels.length - 1, i));
            hover.setAttribute("x1", x(current)); hover.setAttribute("x2", x(current));
            hover.setAttribute("visibility", "visible");
            series.forEach((sr, k) => {
                hoverDots[k].setAttribute("cx", x(current));
                hoverDots[k].setAttribute("cy", y(sr.values[current]));
                hoverDots[k].setAttribute("visibility", "visible");
            });
            const rows = series.map((sr) => ({ label: sr.label, color: sr.color, value: fmt(sr.values[current]) }));
            if (cx === undefined) [cx, cy] = anchorOf(hoverDots[0]);
            showTooltip(longLabels[current], rows, cx, cy);
        };
        const hide = () => { hover.setAttribute("visibility", "hidden"); hoverDots.forEach((d) => d.setAttribute("visibility", "hidden")); hideTooltip(); };
        overlay.addEventListener("pointermove", (ev) => {
            const pt = svg.getBoundingClientRect();
            const scaleX = W / pt.width;
            const px = (ev.clientX - pt.left) * scaleX;
            const i = labels.length === 1 ? 0 : Math.round(((px - m.l) / iw) * (labels.length - 1));
            show(i, ev.clientX, ev.clientY);
        });
        overlay.addEventListener("pointerleave", hide);
        overlay.addEventListener("focus", () => show(current));
        overlay.addEventListener("blur", hide);
        overlay.addEventListener("keydown", (ev) => {
            if (ev.key === "ArrowRight") { show(current + 1); ev.preventDefault(); }
            if (ev.key === "ArrowLeft") { show(current - 1); ev.preventDefault(); }
            if (ev.key === "Escape") hide();
        });
        svg.append(overlay);
        return svg;
    }

    // -----------------------------------------------------------------------
    // Columnas apiladas (por intervalo de tiempo o por tramo)
    // -----------------------------------------------------------------------

    function stackedColumns({ labels, longLabels, series, fmt, ariaLabel, maxLabels = 12, labelMax = false, height = 280, width }) {
        const W = width || 960, H = height, m = { l: 64, r: 16, t: labelMax ? 24 : 12, b: 32 };
        const iw = W - m.l - m.r, ih = H - m.t - m.b;
        const totals = labels.map((_, i) => series.reduce((acc, sr) => acc + sr.values[i], 0));
        const scale = niceScale(Math.max(0, ...totals));
        const band = iw / labels.length;
        const bw = Math.max(2, Math.min(48, band * 0.68));
        const x = (i) => m.l + band * i + band / 2;
        const y = (v) => m.t + ih - (v / scale.max) * ih;
        const GAP = 2;
        const svg = svgRoot(W, H, ariaLabel);

        yAxis(svg, scale, y, m.l, m.l + iw, compact);
        xLabels(svg, labels, x, H - 8, maxLabels);

        const groups = [];
        labels.forEach((_, i) => {
            const g = s("g", {});
            let base = 0;
            const visible = series.map((sr, k) => ({ sr, k, v: sr.values[i] })).filter((e) => e.v > 0);
            visible.forEach((e, idx) => {
                const y0 = y(base), y1 = y(base + e.v);
                const isTop = idx === visible.length - 1;
                const hgt = Math.max(1, y0 - y1 - (idx > 0 ? GAP : 0));
                const top = y0 - (idx > 0 ? GAP : 0) - hgt;
                g.append(s("path", { d: isTop ? barPath(x(i) - bw / 2, top, bw, hgt, 4) : `M${x(i) - bw / 2},${top}h${bw}v${hgt}h${-bw}Z`, fill: e.sr.color }));
                base += e.v;
            });
            if (labelMax && totals[i] > 0 && totals[i] === Math.max(...totals)) {
                g.append(s("text", { class: "value-label", x: x(i), y: y(totals[i]) - 6, "text-anchor": "middle", text: fmt(totals[i]) }));
            }
            const hit = s("rect", { class: "bar-hit", x: x(i) - band / 2, y: m.t, width: band, height: ih, tabindex: "0", "aria-label": `${longLabels[i]}: ${fmt(totals[i])}` });
            const showCol = (cx, cy) => {
                groups.forEach((gg, j) => gg.classList.toggle("is-dim", j !== i));
                const rows = series.map((sr) => ({ label: sr.label, color: sr.color, value: fmt(sr.values[i]) }));
                if (series.length > 1) rows.push({ label: "Total", value: fmt(totals[i]) });
                if (cx === undefined) [cx, cy] = anchorOf(hit);
                showTooltip(longLabels[i], rows, cx, cy);
            };
            const hideCol = () => { groups.forEach((gg) => gg.classList.remove("is-dim")); hideTooltip(); };
            hit.addEventListener("pointermove", (ev) => showCol(ev.clientX, ev.clientY));
            hit.addEventListener("pointerleave", hideCol);
            hit.addEventListener("focus", () => showCol());
            hit.addEventListener("blur", hideCol);
            groups.push(g);
            svg.append(g, hit);
        });
        return svg;
    }

    // -----------------------------------------------------------------------
    // Barras horizontales apiladas (opcionalmente al 100 %)
    // -----------------------------------------------------------------------

    function hBars({ rows, series, fmt, ariaLabel, percent = false, endLabel, width }) {
        const W = width || 960;
        const rowH = 30, gapY = 18, labelW = Math.min(190, Math.round(W * 0.3));
        const m = { l: labelW, r: Math.min(120, Math.round(W * 0.2)), t: 8, b: percent ? 8 : 28 };
        const H = m.t + m.b + rows.length * (rowH + gapY) - gapY;
        const iw = W - m.l - m.r;
        const totals = rows.map((r) => r.values.reduce((a, b) => a + b, 0));
        const scale = percent ? { max: 100, ticks: [] } : niceScale(Math.max(0, ...totals));
        const xv = (v) => m.l + (v / scale.max) * iw;
        const svg = svgRoot(W, H, ariaLabel);
        const GAP = 2;

        if (!percent) {
            for (const t of scale.ticks) {
                svg.append(s("line", { class: t === 0 ? "axis-line" : "grid-line", x1: xv(t), x2: xv(t), y1: m.t, y2: H - m.b }));
                svg.append(s("text", { x: xv(t), y: H - 8, "text-anchor": "middle", text: compact(t) }));
            }
        }
        const groups = [];
        rows.forEach((row, i) => {
            const top = m.t + i * (rowH + gapY);
            const g = s("g", {});
            svg.append(fitText({ x: m.l - 12, y: top + rowH / 2 + 4, "text-anchor": "end", class: "value-label" }, row.label, m.l - 16));
            const total = totals[i];
            let acc = 0;
            const visible = row.values.map((v, k) => ({ v, k })).filter((e) => e.v > 0);
            if (!visible.length) {
                g.append(s("rect", { x: m.l, y: top, width: iw, height: rowH, rx: 4, fill: "none", stroke: token("--line"), "stroke-dasharray": "4 4" }));
            }
            visible.forEach((e, idx) => {
                const value = percent ? (e.v / total) * 100 : e.v;
                const x0 = xv(acc) + (idx > 0 ? GAP : 0);
                const w = Math.max(1, xv(acc + value) - xv(acc) - (idx > 0 ? GAP : 0));
                const isEnd = idx === visible.length - 1;
                g.append(s("path", { d: isEnd ? barPath(x0, top, w, rowH, 4, true) : `M${x0},${top}h${w}v${rowH}h${-w}Z`, fill: series[e.k].color }));
                if (percent && w > 44) {
                    g.append(s("text", { x: x0 + w / 2, y: top + rowH / 2 + 4, "text-anchor": "middle", style: { fill: series[e.k].color === C.neutral ? C.ink : "#ffffff", fontWeight: "600" }, text: `${Math.round(value)} %` }));
                }
                acc += value;
            });
            svg.append(s("text", { x: m.l + iw + 10, y: top + rowH / 2 + 4, class: "value-label", text: endLabel ? endLabel(total, row) : fmt(total) }));
            const hit = s("rect", { class: "bar-hit", x: 0, y: top - gapY / 2, width: W, height: rowH + gapY, tabindex: "0", "aria-label": `${row.label}: ${endLabel ? endLabel(total, row) : fmt(total)}` });
            const showRow = (cx, cy) => {
                groups.forEach((gg, j) => gg.classList.toggle("is-dim", j !== i));
                const rowsTt = series.map((sr, k) => ({
                    label: sr.label, color: sr.color,
                    value: percent && total ? `${fmt(row.values[k])} (${Math.round((row.values[k] / total) * 100)} %)` : fmt(row.values[k]),
                }));
                if (cx === undefined) [cx, cy] = anchorOf(hit);
                showTooltip(row.label, rowsTt, cx, cy);
            };
            const hideRow = () => { groups.forEach((gg) => gg.classList.remove("is-dim")); hideTooltip(); };
            hit.addEventListener("pointermove", (ev) => showRow(ev.clientX, ev.clientY));
            hit.addEventListener("pointerleave", hideRow);
            hit.addEventListener("focus", () => showRow());
            hit.addEventListener("blur", hideRow);
            groups.push(g);
            svg.append(g, hit);
        });
        return svg;
    }

    // -----------------------------------------------------------------------
    // Rango p50–p95 por modelo
    // -----------------------------------------------------------------------

    function rangeDots({ rows, ariaLabel, width }) {
        const W = width || 960;
        const rowH = 30, gapY = 14, labelW = Math.min(230, Math.round(W * 0.36));
        const m = { l: labelW, r: 64, t: 8, b: 28 };
        const H = m.t + m.b + rows.length * (rowH + gapY) - gapY;
        const iw = W - m.l - m.r;
        const scale = niceScale(Math.max(...rows.map((r) => r.p95)));
        const xv = (v) => m.l + (v / scale.max) * iw;
        const svg = svgRoot(W, H, ariaLabel);
        for (const t of scale.ticks) {
            svg.append(s("line", { class: t === 0 ? "axis-line" : "grid-line", x1: xv(t), x2: xv(t), y1: m.t, y2: H - m.b }));
            svg.append(s("text", { x: xv(t), y: H - 8, "text-anchor": "middle", text: ms(t) }));
        }
        rows.forEach((r, i) => {
            const cy = m.t + i * (rowH + gapY) + rowH / 2;
            svg.append(fitText({ x: m.l - 12, y: cy - 2, "text-anchor": "end", class: "value-label" }, r.model, m.l - 16));
            svg.append(fitText({ x: m.l - 12, y: cy + 12, "text-anchor": "end" }, `${providerLabel(r.provider)} · ${num(r.calls)} llamadas`, m.l - 16));
            svg.append(s("line", { x1: xv(r.p50), x2: xv(r.p95), y1: cy, y2: cy, stroke: C.neutral, "stroke-width": 3, "stroke-linecap": "round" }));
            svg.append(s("circle", { cx: xv(r.p50), cy, r: 6, fill: C.s1, stroke: C.surface, "stroke-width": 2 }));
            svg.append(s("circle", { cx: xv(r.p95), cy, r: 6, fill: C.s2, stroke: C.surface, "stroke-width": 2 }));
            svg.append(s("text", { x: xv(r.p95) + 12, y: cy + 4, class: "value-label", text: ms(r.p95) }));
            const hit = s("rect", { class: "bar-hit", x: 0, y: cy - rowH / 2 - gapY / 2, width: W, height: rowH + gapY, tabindex: "0", "aria-label": `${r.model}: p50 ${ms(r.p50)}, p95 ${ms(r.p95)}` });
            const showRow = (cx, cyy) => {
                if (cx === undefined) [cx, cyy] = anchorOf(hit);
                showTooltip(`${r.model}`, [
                    { label: "Mediana (p50)", color: C.s1, value: ms(r.p50) },
                    { label: "p95", color: C.s2, value: ms(r.p95) },
                    { label: "Llamadas exitosas", value: num(r.calls) },
                ], cx, cyy);
            };
            hit.addEventListener("pointermove", (ev) => showRow(ev.clientX, ev.clientY));
            hit.addEventListener("pointerleave", hideTooltip);
            hit.addEventListener("focus", () => showRow());
            hit.addEventListener("blur", hideTooltip);
            svg.append(hit);
        });
        return svg;
    }

    function sparkline(values, color) {
        const W = 96, H = 28, pad = 3;
        const clean = values.map((v) => (v === null || v === undefined ? 0 : v));
        const max = Math.max(...clean);
        const svg = s("svg", { class: "kpi-spark", viewBox: `0 0 ${W} ${H}`, "aria-hidden": "true" });
        if (!clean.length || max <= 0) {
            svg.append(s("line", { x1: pad, x2: W - pad, y1: H - pad, y2: H - pad, stroke: C.neutral, "stroke-width": 2, "stroke-linecap": "round" }));
            return svg;
        }
        const x = (i) => pad + (i * (W - 2 * pad)) / Math.max(1, clean.length - 1);
        const y = (v) => H - pad - (v / max) * (H - 2 * pad);
        svg.append(s("path", { d: clean.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join(""), fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
        svg.append(s("circle", { cx: x(clean.length - 1), cy: y(clean[clean.length - 1]), r: 3, fill: color }));
        return svg;
    }

    // -----------------------------------------------------------------------
    // Resumen
    // -----------------------------------------------------------------------

    function delta(value, previous, lowerIsBetter) {
        if (value === null || previous === null || previous === undefined) {
            return h("span", { class: "kpi-delta", text: "Sin datos del período anterior" });
        }
        if (previous === 0) {
            return h("span", { class: "kpi-delta", text: value === 0 ? "Igual que el período anterior" : "Sin uso en el período anterior" });
        }
        const change = Math.round(((value - previous) / previous) * 100);
        if (change === 0) return h("span", { class: "kpi-delta" }, icon("trending_flat"), "Igual que el período anterior");
        const up = change > 0;
        const better = lowerIsBetter ? !up : up;
        return h("span", { class: "kpi-delta", title: better ? "Mejor que el período anterior" : "Peor que el período anterior" },
            icon(up ? "trending_up" : "trending_down"),
            `${up ? "+" : "−"}${nf.format(Math.abs(change))} % vs. período anterior`);
    }

    function kpiCard({ label, iconName, value, unit, foot, spark }) {
        return h("div", { class: "kpi" },
            h("span", { class: "kpi-label" }, icon(iconName), label),
            h("span", { class: "kpi-value" }, value, unit ? h("span", { class: "kpi-unit", text: unit }) : null),
            h("div", { class: "kpi-foot" }, foot, spark || null));
    }

    function renderSummary(d) {
        const k = d.kpis;
        const rangeName = { "24h": "últimas 24 h", "7d": "últimos 7 días", "30d": "últimos 30 días" }[d.range];
        const p95 = k.latency_p95.value;
        $("kpis").replaceChildren(
            kpiCard({ label: "Llamadas a la IA", iconName: "bolt", value: num(k.calls.value), foot: delta(k.calls.value, k.calls.previous, false), spark: sparkline(k.calls.spark, C.s1) }),
            kpiCard({ label: "Tokens consumidos", iconName: "token", value: compact(k.tokens.value), foot: delta(k.tokens.value, k.tokens.previous, false), spark: sparkline(k.tokens.spark, C.s1) }),
            kpiCard({ label: "Latencia p95", iconName: "timer", value: p95 === null ? "—" : ms(p95).split(" ")[0], unit: p95 === null ? null : ms(p95).split(" ")[1], foot: delta(p95, k.latency_p95.previous, true) }),
            kpiCard({ label: "Tasa de errores", iconName: "error", value: k.error_rate.value === null ? "—" : nf.format(k.error_rate.value), unit: k.error_rate.value === null ? null : "%", foot: delta(k.error_rate.value, k.error_rate.previous, true), spark: sparkline(k.error_rate.spark, C.critical) }),
        );

        const labels = d.buckets.map((b) => bucketLabel(b, d.unit));
        const longLabels = d.buckets.map((b) => bucketLabel(b, d.unit, true));
        const unitName = d.unit === "hour" ? "Por hora" : "Por día";

        const provSeries = d.usage.tokens_by_provider.map((sr, i) => ({ label: providerLabel(sr.key), color: SERIES[i], values: sr.values }));
        const anyTokens = provSeries.some((sr) => sr.values.some((v) => v > 0));
        chartCard($("chart-tokens"), {
            title: "Tokens por proveedor",
            subtitle: `${unitName}. Los proveedores que más consumen; el resto se agrupa en "Otros".`,
            legend: provSeries.map((sr) => ({ label: sr.label, color: sr.color, line: true })),
            svg: anyTokens ? lineChart({ labels, longLabels, series: provSeries, fmt: num, ariaLabel: `Tokens por proveedor, ${rangeName}`, width: widthOf("chart-tokens") }) : null,
            table: anyTokens ? {
                headers: ["Período", ...provSeries.map((sr) => sr.label)],
                rows: longLabels.map((l, i) => [l, ...provSeries.map((sr) => num(sr.values[i]))]),
            } : null,
            empty: anyTokens ? null : { icon: "query_stats", text: "No hubo consumo de tokens en este período. Los análisis con ?mock=true no usan la IA." },
        });

        const o = d.usage.outcomes;
        const failed = o.server_error.map((v, i) => v + o.error[i]);
        const outSeries = [
            { label: "Exitosas", color: C.good, values: o.ok },
            { label: "Limitadas por el proveedor (429)", color: C.warning, values: o.rate_limited },
            { label: "Con error (5xx, key o modelo)", color: C.critical, values: failed },
        ];
        const anyCalls = d.usage.calls.some((v) => v > 0);
        chartCard($("chart-outcomes"), {
            title: "Llamadas por resultado",
            subtitle: `${unitName}. Cada reintento cuenta como una llamada.`,
            legend: outSeries.map((sr) => ({ label: sr.label, color: sr.color })),
            svg: anyCalls ? stackedColumns({ labels, longLabels, series: outSeries, fmt: num, ariaLabel: `Llamadas por resultado, ${rangeName}`, width: widthOf("chart-outcomes") }) : null,
            table: anyCalls ? {
                headers: ["Período", ...outSeries.map((sr) => sr.label)],
                rows: longLabels.map((l, i) => [l, ...outSeries.map((sr) => num(sr.values[i]))]),
            } : null,
            empty: anyCalls ? null : { icon: "bolt", text: "No hubo llamadas a la IA en este período." },
        });
    }

    function renderAlerts(d) {
        $("alerts").replaceChildren(...d.alerts.map((a) => {
            const exhausted = a.status === "exhausted";
            return h("div", { class: `alert ${exhausted ? "alert-danger" : "alert-warning"} admin-alert`, role: "alert" },
                h("p", { class: "mb-0" },
                    h("strong", { text: a.label }),
                    exhausted ? " está agotada: se usa la siguiente conexión por prioridad."
                        : ` está cerca del límite (${nf.format(a.usage_pct)} % del umbral más usado).`));
        }));
    }

    // -----------------------------------------------------------------------
    // Conexiones
    // -----------------------------------------------------------------------

    function statusBadge(status) {
        const st = STATUS[status] || { icon: "help", label: status };
        return h("span", { class: `status status-${status}` }, icon(st.icon), st.label);
    }

    function meter(label, value, threshold) {
        if (!threshold) {
            return h("div", {}, h("div", { class: "meter-top" }, h("b", { text: label }), h("span", { class: "meter-value", text: `${compact(value)} · sin umbral` })));
        }
        const ratio = value / threshold;
        const level = ratio >= 1 ? "crit" : ratio >= 0.8 ? "warn" : "";
        return h("div", {},
            h("div", { class: "meter-top" }, h("b", { text: label }), h("span", { class: "meter-value", text: `${compact(value)} / ${compact(threshold)}` })),
            h("div", {
                class: "meter-track", role: "meter", "aria-label": `${label}: ${num(value)} de ${num(threshold)}`,
                "aria-valuemin": "0", "aria-valuemax": String(threshold), "aria-valuenow": String(Math.min(value, threshold)),
            },
            h("span", { class: `meter-fill ${level}`, style: { width: `${Math.min(100, ratio * 100)}%` } }),
            h("span", { class: "meter-mark", title: "80 % del umbral" })));
    }

    function enabledSwitch(c) {
        const id = `switch-${c.id}`;
        const input = h("input", { type: "checkbox", id, checked: c.enabled });
        input.addEventListener("change", async () => {
            const body = new FormData();
            body.append("enabled", input.checked ? "true" : "false");
            try {
                const r = await fetch(api(`/keys/${c.id}/enabled`), { method: "POST", body });
                if (r.status === 401) return goToLogin();
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                toast(`${c.label}: ${input.checked ? "habilitada" : "deshabilitada"}.`);
                loadMetrics();
            } catch (err) {
                input.checked = !input.checked;
                toast(`No se pudo cambiar el estado de ${c.label} (${err.message}).`);
            }
        });
        return h("label", { class: "switch", for: id },
            input, h("span", { class: "slider" }),
            h("span", { class: "switch-label on", text: "Sí" }),
            h("span", { class: "switch-label off", text: "No" }),
            h("span", { class: "visually-hidden", text: `Habilitar ${c.label}` }));
    }

    function renderConnections(d) {
        const q = $("conn-search").value.trim().toLowerCase();
        const status = $("conn-status").value;
        const list = d.connections.filter((c) =>
            (!status || c.status === status)
            && (!q || `${c.label} ${c.provider} ${c.model}`.toLowerCase().includes(q)));
        const body = $("connections-body");
        if (!d.connections.length) {
            body.replaceChildren(h("tr", {}, h("td", { colspan: "7" },
                h("div", { class: "chart-empty" }, icon("power_off"), "Todavía no hay ninguna conexión de IA cargada."))));
        } else if (!list.length) {
            body.replaceChildren(h("tr", {}, h("td", { colspan: "7", class: "admin-muted", text: "Ninguna conexión coincide con el filtro." })));
        } else {
            body.replaceChildren(...list.map((c) => h("tr", { class: c.enabled ? null : "is-disabled" },
                h("td", { class: "num", text: c.priority }),
                h("td", {},
                    h("span", { class: "cell-title" }, c.label, " ",
                        c.managed_by_env ? h("span", { class: "badge badge-s-info badge-sm", title: "Se restaura desde .env (AI_API_KEY) en cada arranque", text: ".env" }) : null),
                    h("code", { text: c.masked_key })),
                h("td", {}, h("span", { class: "cell-title", text: providerLabel(c.provider) }), h("code", { text: c.model })),
                h("td", {}, statusBadge(c.status)),
                h("td", {}, h("div", { class: "meters" },
                    meter("RPM", c.minute_request_count, c.rpm_threshold),
                    meter("TPM", c.minute_token_count, c.tpm_threshold),
                    meter("RPD", c.request_count, c.rpd_threshold),
                    meter("TPD", c.total_token_count, c.tpd_threshold))),
                h("td", {}, CAN_EDIT ? enabledSwitch(c)
                    : h("span", { class: "readonly-value" }, icon(c.enabled ? "toggle_on" : "toggle_off"), c.enabled ? "Sí" : "No")),
                h("td", {}, CAN_EDIT ? h("div", { class: "row-actions" },
                    h("button", { type: "button", class: "btn btn-sm btn-outline-secondary btn-icon-only", title: "Editar", "aria-label": `Editar ${c.label}`, onclick: () => openConnectionModal(c) }, icon("edit")),
                    h("button", { type: "button", class: "btn btn-sm btn-outline-danger btn-icon-only", title: "Borrar", "aria-label": `Borrar ${c.label}`, onclick: () => openDeleteModal(c) }, icon("delete")))
                    : h("span", { class: "readonly-value", title: "Solo lectura" }, icon("lock"))),
            )));
        }

        const withTokens = d.connections.filter((c) => c.prompt_tokens_today + c.output_tokens_today > 0);
        const tokenSeries = [{ label: "Entrada (prompt)", color: C.s1 }, { label: "Salida (respuesta)", color: C.s2 }];
        chartCard($("chart-conn-tokens"), {
            title: "Tokens de hoy por conexión",
            subtitle: "Día UTC, el mismo que usan los umbrales diarios.",
            legend: tokenSeries,
            svg: withTokens.length ? hBars({
                rows: withTokens.map((c) => ({ label: c.label, values: [c.prompt_tokens_today, c.output_tokens_today] })),
                series: tokenSeries, fmt: num, ariaLabel: "Tokens de hoy por conexión", width: widthOf("chart-conn-tokens"),
            }) : null,
            table: withTokens.length ? {
                headers: ["Conexión", "Entrada", "Salida", "Total"],
                rows: withTokens.map((c) => [c.label, num(c.prompt_tokens_today), num(c.output_tokens_today), num(c.prompt_tokens_today + c.output_tokens_today)]),
            } : null,
            empty: withTokens.length ? null : { icon: "token", text: "Ninguna conexión consumió tokens hoy." },
        });

        const lat = d.latency_by_model;
        chartCard($("chart-latency"), {
            title: "Latencia por modelo",
            subtitle: "Mediana (p50) y p95 de las llamadas exitosas del período.",
            legend: [{ label: "Mediana (p50)", color: C.s1 }, { label: "p95", color: C.s2 }],
            svg: lat.length ? rangeDots({ rows: lat, ariaLabel: "Latencia por modelo", width: widthOf("chart-latency") }) : null,
            table: lat.length ? {
                headers: ["Modelo", "Llamadas", "p50", "p95"],
                rows: lat.map((r) => [`${r.model} (${providerLabel(r.provider)})`, num(r.calls), ms(r.p50), ms(r.p95)]),
            } : null,
            empty: lat.length ? null : { icon: "timer", text: "No hubo llamadas exitosas en este período." },
        });
    }

    // -----------------------------------------------------------------------
    // Uso del producto
    // -----------------------------------------------------------------------

    function renderProduct(d) {
        const p = d.product;
        const sources = Object.entries(p.by_source).filter(([, v]) => v > 0)
            .map(([k, v]) => `${num(v)} ${LABELS.source[k] || k}`).join(" · ");
        $("product-kpis").replaceChildren(
            kpiCard({ label: "Diagnósticos vigentes", iconName: "fact_check", value: num(p.analyses), foot: h("span", { class: "kpi-delta", text: sources || "Sin diagnósticos" }) }),
            kpiCard({ label: "Puntaje promedio", iconName: "speed", value: p.avg_score === null ? "—" : num(p.avg_score), unit: p.avg_score === null ? null : "/ 100", foot: h("span", { class: "kpi-delta", text: "Puntaje orientativo del diagnóstico" }) }),
            kpiCard({ label: "Propuestas de adaptación", iconName: "auto_fix_high", value: num(p.proposals), foot: h("span", { class: "kpi-delta", text: "Generadas con IA por barrera" }) }),
            kpiCard({ label: "Aceptación docente", iconName: "thumb_up", value: p.acceptance_rate === null ? "—" : num(p.acceptance_rate), unit: p.acceptance_rate === null ? null : "%", foot: h("span", { class: "kpi-delta", text: "Aceptadas o editadas sobre las revisadas" }) }),
        );

        const histLabels = p.score_histogram.map((_, i) => (i === 9 ? "90–100" : `${i * 10}–${i * 10 + 9}`));
        const anyScore = p.score_histogram.some((v) => v > 0);
        chartCard($("chart-scores"), {
            title: "Distribución de puntajes",
            subtitle: "Cantidad de diagnósticos por tramo de puntaje.",
            svg: anyScore ? stackedColumns({
                labels: histLabels, longLabels: histLabels.map((l) => `Puntaje ${l}`),
                series: [{ label: "Diagnósticos", color: C.s1, values: p.score_histogram }],
                fmt: num, ariaLabel: "Distribución de puntajes de los diagnósticos", labelMax: true, height: 260, width: widthOf("chart-scores"), maxLabels: 10,
            }) : null,
            table: anyScore ? { headers: ["Tramo", "Diagnósticos"], rows: histLabels.map((l, i) => [l, num(p.score_histogram[i])]) } : null,
            empty: anyScore ? null : { icon: "speed", text: "No hay diagnósticos vigentes." },
        });

        const sevSeries = [
            { label: LABELS.severity.para_revisar, color: C.warning },
            { label: LABELS.severity.barrera_detectada, color: C.critical },
        ];
        const barrierRows = Object.entries(p.barriers).map(([cat, v]) => ({
            label: LABELS.category[cat] || cat, values: [v.para_revisar, v.barrera_detectada],
        }));
        const anyBarrier = barrierRows.some((r) => r.values.some((v) => v > 0));
        chartCard($("chart-barriers"), {
            title: "Barreras por categoría",
            subtitle: "Según la gravedad que asignó el diagnóstico.",
            legend: sevSeries,
            svg: anyBarrier ? hBars({ rows: barrierRows, series: sevSeries, fmt: num, ariaLabel: "Barreras detectadas por categoría y gravedad", width: widthOf("chart-barriers") }) : null,
            table: anyBarrier ? {
                headers: ["Categoría", ...sevSeries.map((sr) => sr.label), "Total"],
                rows: barrierRows.map((r) => [r.label, ...r.values.map(num), num(r.values[0] + r.values[1])]),
            } : null,
            empty: anyBarrier ? null : { icon: "verified", text: "No hay barreras en los diagnósticos vigentes." },
        });

        const decSeries = [
            { label: LABELS.decision.aceptada, color: C.good },
            { label: LABELS.decision.editada, color: C.s1 },
            { label: LABELS.decision.descartada, color: C.critical },
            { label: LABELS.decision.pendiente, color: C.neutral },
        ];
        const decRows = Object.entries(p.decisions).map(([type, v]) => ({
            label: LABELS.adaptation[type] || type, values: [v.aceptada, v.editada, v.descartada, v.pendiente],
        }));
        const anyDecision = decRows.some((r) => r.values.some((v) => v > 0));
        chartCard($("chart-decisions"), {
            title: "Decisiones del docente por tipo de adaptación",
            subtitle: "Proporción de cada decisión sobre las propuestas vigentes.",
            legend: decSeries,
            svg: anyDecision ? hBars({
                rows: decRows, series: decSeries, fmt: num, percent: true,
                endLabel: (total) => (total ? `${num(total)} propuesta${total === 1 ? "" : "s"}` : "Sin propuestas"),
                ariaLabel: "Decisiones del docente por tipo de adaptación", width: widthOf("chart-decisions"),
            }) : null,
            table: anyDecision ? {
                headers: ["Tipo", ...decSeries.map((sr) => sr.label), "Total"],
                rows: decRows.map((r) => [r.label, ...r.values.map(num), num(r.values.reduce((a, b) => a + b, 0))]),
            } : null,
            empty: anyDecision ? null : { icon: "rule", text: "Todavía no hay propuestas de adaptación vigentes." },
        });

        const body = $("recent-body");
        if (!p.recent.length) {
            body.replaceChildren(h("tr", {}, h("td", { colspan: "7", class: "admin-muted", text: "No hay diagnósticos vigentes." })));
            return;
        }
        body.replaceChildren(...p.recent.map((r) => h("tr", {},
            h("td", { text: fullFmt.format(new Date(r.created_at)) }),
            h("td", {}, h("span", { class: "badge badge-s-default badge-sm", text: LABELS.source[r.source_type] || r.source_type })),
            h("td", { class: "num" }, h("strong", { text: r.score ?? "—" })),
            h("td", { class: "num", text: num(r.barriers) }),
            h("td", {}, h("span", { class: "cell-title", text: r.provider ? providerLabel(r.provider) : "—" }), r.model ? h("code", { text: r.model }) : null),
            h("td", { class: "num", text: ms(r.latency_ms) }),
            h("td", { class: "num", text: num(r.total_tokens) }),
        )));
    }

    // -----------------------------------------------------------------------
    // Modales de conexión
    // -----------------------------------------------------------------------

    // Si se llega por el enlace directo (#nueva-conexion), el modal se muestra sin animación
    if (location.hash === "#nueva-conexion") $("connection-modal").classList.remove("fade");
    const connectionModal = new bootstrap.Modal($("connection-modal"));
    const deleteModal = new bootstrap.Modal($("delete-modal"));
    const form = $("connection-form");

    function applyPreset(id) {
        const p = (CONFIG.presets || []).find((x) => x.id === id);
        if (!p) return;
        ObSelect.set("f-provider", p.id);
        form.base_url.value = p.base_url || "";
        form.model.value = p.model || "";
        form.rpm_threshold.value = p.rpm ?? "";
        form.tpm_threshold.value = p.tpm ?? "";
        form.rpd_threshold.value = p.rpd ?? "";
        form.tpd_threshold.value = p.tpd ?? "";
        form.extra_params.value = Object.keys(p.extra_params || {}).length ? JSON.stringify(p.extra_params) : "";
        $("preset-notes").textContent = p.notes || "";
    }

    function openConnectionModal(c) {
        form.reset();
        const editing = Boolean(c);
        form.action = editing ? `${PREFIX}/keys/${c.id}/update` : `${PREFIX}/keys`;
        $("connection-modal-title").textContent = editing ? `Editar ${c.label}` : "Agregar conexión";
        $("connection-submit").textContent = editing ? "Guardar cambios" : "Agregar";
        $("connection-modal-lead").textContent = editing
            ? `Proveedor ${providerLabel(c.provider)} · prioridad ${c.priority}.`
            : "Elegí un proveedor para completar el modelo, la base URL y los umbrales sugeridos.";
        $("enabled-group").hidden = !editing;
        $("preset-notes").textContent = "";
        form.key_value.required = !editing;
        $("f-key-help").textContent = editing
            ? `Dejala vacía para conservar la actual (${c.masked_key}).`
            : "Se guarda cifrada en la base y nunca se vuelve a mostrar completa.";
        if (editing) {
            form.label.value = c.label;
            form.priority.value = c.priority;
            ObSelect.set("f-provider", c.provider);
            form.model.value = c.model;
            form.base_url.value = c.base_url || "";
            form.extra_params.value = c.extra_params && c.extra_params !== "{}" ? c.extra_params : "";
            form.rpm_threshold.value = c.rpm_threshold ?? "";
            form.tpm_threshold.value = c.tpm_threshold ?? "";
            form.rpd_threshold.value = c.rpd_threshold ?? "";
            form.tpd_threshold.value = c.tpd_threshold ?? "";
            form.enabled.checked = c.enabled;
        } else {
            applyPreset("groq");
        }
        connectionModal.show();
    }

    function openDeleteModal(c) {
        $("delete-form").action = `${PREFIX}/keys/${c.id}/delete`;
        $("delete-name").textContent = c.label;
        $("delete-env-note").hidden = !c.managed_by_env;
        deleteModal.show();
    }

    // Antes de enviar: el JSON de parámetros extra se valida acá para no perder lo cargado
    form.addEventListener("submit", (ev) => {
        const raw = form.extra_params.value.trim();
        form.extra_params.setCustomValidity("");
        if (raw) {
            try {
                const parsed = JSON.parse(raw);
                if (typeof parsed !== "object" || Array.isArray(parsed) || parsed === null) throw new Error();
            } catch {
                form.extra_params.setCustomValidity('Tiene que ser un objeto JSON, por ejemplo {"reasoning_effort": "low"}.');
            }
        }
        form.querySelectorAll(".form-control").forEach((input) => input.classList.toggle("is-invalid", !input.checkValidity()));
        if (!form.checkValidity()) {
            ev.preventDefault();
            form.reportValidity();
        }
    });
    form.addEventListener("input", (ev) => {
        if (ev.target.classList.contains("is-invalid") && ev.target.checkValidity()) ev.target.classList.remove("is-invalid");
    });
    form.extra_params.addEventListener("input", () => form.extra_params.setCustomValidity(""));

    // -----------------------------------------------------------------------
    // Usuarios del panel (solo admins)
    // -----------------------------------------------------------------------

    const ROLE_INFO = {
        admin: { icon: "shield_person", label: "Admin" },
        viewer: { icon: "visibility", label: "Solo lectura" },
    };
    const confirmModal = new bootstrap.Modal($("confirm-modal"));

    function askConfirm({ title, text, ok }) {
        return new Promise((resolve) => {
            $("confirm-modal-title").textContent = title;
            $("confirm-modal-text").textContent = text;
            const okBtn = $("confirm-modal-ok");
            okBtn.textContent = ok;
            let accepted = false;
            const onOk = () => { accepted = true; confirmModal.hide(); };
            okBtn.addEventListener("click", onOk, { once: true });
            $("confirm-modal").addEventListener("hidden.bs.modal", () => {
                okBtn.removeEventListener("click", onOk);
                resolve(accepted);
            }, { once: true });
            confirmModal.show();
        });
    }

    async function membersApi(method, path = "", body) {
        const r = await fetch(api(`/api/members${path}`), {
            method,
            headers: { Accept: "application/json", ...(body ? { "Content-Type": "application/json" } : {}) },
            body: body ? JSON.stringify(body) : undefined,
        });
        if (r.status === 401) { goToLogin(); return null; }
        const data = await r.json().catch(() => ({}));
        if (!r.ok) {
            const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(" ") : data.detail;
            throw new Error(detail || `HTTP ${r.status}`);
        }
        return data;
    }

    function memberFeedback(text, kind = "danger") {
        const box = $("member-feedback");
        box.replaceChildren(text ? h("div", { class: `alert alert-${kind}`, role: kind === "danger" ? "alert" : "status" }, h("p", { class: "mb-0", text })) : "");
    }

    function roleBadge(role) {
        const info = ROLE_INFO[role] || { icon: "help", label: role };
        return h("span", { class: `role-badge role-${role}` }, icon(info.icon), info.label);
    }

    function memberRow(m) {
        const isMe = ME.kind === "github" && ME.username.toLowerCase() === m.github_username.toLowerCase();
        const avatar = m.avatar_url
            ? h("img", { class: "member-avatar", src: m.avatar_url, alt: "", width: "36", height: "36" })
            : h("span", { class: "member-avatar member-avatar-empty", "aria-hidden": "true" }, icon("person"));
        const toggleRole = h("button", {
            type: "button", class: "btn btn-sm btn-outline-secondary", disabled: isMe,
            onclick: () => updateMember(m, { role: m.role === "admin" ? "viewer" : "admin" },
                `@${m.github_username} ahora es ${m.role === "admin" ? "de solo lectura" : "admin"}.`),
        }, icon(m.role === "admin" ? "visibility" : "shield_person"), m.role === "admin" ? "Pasar a solo lectura" : "Hacer admin");
        const accessId = `member-enabled-${m.id}`;
        const access = h("input", { type: "checkbox", id: accessId, checked: m.enabled, disabled: isMe });
        access.addEventListener("change", () => updateMember(m, { enabled: access.checked },
            `@${m.github_username}: acceso ${access.checked ? "habilitado" : "suspendido"}.`, () => { access.checked = !access.checked; }));
        return h("tr", { class: m.enabled ? null : "is-disabled" },
            h("td", {}, h("div", { class: "member-cell" }, avatar,
                h("div", {},
                    h("a", { class: "cell-title", href: `https://github.com/${encodeURIComponent(m.github_username)}`, target: "_blank", rel: "noopener" },
                        `@${m.github_username}`, isMe ? h("span", { class: "badge badge-s-info badge-sm me-badge", text: "Vos" }) : null),
                    h("span", { class: "cell-sub", text: m.display_name || "Todavía no ingresó" })))),
            h("td", {}, roleBadge(m.role)),
            h("td", {}, h("label", { class: "switch", for: accessId }, access, h("span", { class: "slider" }),
                h("span", { class: "switch-label on", text: "Activo" }), h("span", { class: "switch-label off", text: "Suspendido" }),
                h("span", { class: "visually-hidden", text: `Acceso de @${m.github_username}` }))),
            h("td", { text: m.last_login_at ? fullFmt.format(new Date(m.last_login_at)) : "Nunca" }),
            h("td", { class: "admin-muted", text: m.added_by || "—" }),
            h("td", {}, h("div", { class: "row-actions" }, isMe ? h("span", { class: "admin-muted small", text: "Tu acceso lo cambia otro admin" }) : [
                toggleRole,
                h("button", {
                    type: "button", class: "btn btn-sm btn-outline-danger btn-icon-only", title: "Quitar acceso",
                    "aria-label": `Quitar el acceso de @${m.github_username}`, onclick: () => removeMember(m),
                }, icon("person_remove")),
            ])),
        );
    }

    function renderMembers(data) {
        if (!data) return;
        const body = $("members-body");
        body.replaceChildren(...(data.members.length ? data.members.map(memberRow)
            : [h("tr", {}, h("td", { colspan: "6", class: "admin-muted", text: "Todavía no hay usuarios de GitHub con acceso." }))]));
        const local = data.local_account;
        $("local-account-note").replaceChildren(icon("key"),
            h("span", {}, "Además, la cuenta ", h("strong", { text: local.username }),
                " del .env es admin y no se puede quitar desde acá",
                local.clerk ? ": entra por Clerk con usuario y contraseña" : "",
                local.local_login ? (local.clerk ? " o por el acceso de respaldo." : " con el formulario de usuario y contraseña.") : "."));
    }

    async function loadMembers() {
        try {
            renderMembers(await membersApi("GET"));
        } catch (err) {
            memberFeedback(`No se pudieron cargar los usuarios (${err.message}).`);
        }
    }

    async function updateMember(m, changes, okText, revert) {
        try {
            renderMembers(await membersApi("PATCH", `/${m.id}`, changes));
            memberFeedback("");
            toast(okText);
        } catch (err) {
            if (revert) revert();
            memberFeedback(err.message);
        }
    }

    async function removeMember(m) {
        const ok = await askConfirm({
            title: "Quitar acceso",
            text: `@${m.github_username} ya no va a poder entrar al panel. Si tiene una sesión abierta, se cierra en su próxima acción.`,
            ok: "Quitar acceso",
        });
        if (!ok) return;
        try {
            renderMembers(await membersApi("DELETE", `/${m.id}`));
            memberFeedback("");
            toast(`Se quitó el acceso de @${m.github_username}.`);
        } catch (err) {
            memberFeedback(err.message);
        }
    }

    $("member-form")?.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const input = $("member-username");
        const username = input.value.trim().replace(/^@/, "");
        if (!username) {
            input.classList.add("is-invalid");
            memberFeedback("Escribí el usuario de GitHub.");
            input.focus();
            return;
        }
        try {
            renderMembers(await membersApi("POST", "", { github_username: username, role: $("member-role").value }));
            input.value = "";
            input.classList.remove("is-invalid");
            memberFeedback("");
            toast(`@${username} ya tiene acceso al panel.`);
        } catch (err) {
            input.classList.add("is-invalid");
            memberFeedback(err.message);
        }
    });
    if ($("member-role") && !$("member-role").value) ObSelect.set("member-role", "viewer");

    // -----------------------------------------------------------------------
    // Carga y refresco
    // -----------------------------------------------------------------------

    const toastNode = new bootstrap.Toast($("toast"), { delay: 3500 });
    function toast(text) {
        $("toast-body").textContent = text;
        toastNode.show();
    }

    function render() {
        const d = state.data;
        if (!d) return;
        renderAlerts(d);
        renderSummary(d);
        renderConnections(d);
        renderProduct(d);
        $("updated-at").textContent = `Actualizado a las ${hourFmt.format(new Date())}`;
    }

    function goToLogin() {
        location.assign(`${PREFIX}/login?next=${encodeURIComponent(location.pathname + location.hash)}`);
    }

    async function loadMetrics() {
        try {
            const tz = new Date().getTimezoneOffset();
            const r = await fetch(api(`/api/metrics?range=${state.range}&tz=${tz}`), { headers: { Accept: "application/json" } });
            if (r.status === 401) return goToLogin();
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            state.data = await r.json();
            render();
        } catch (err) {
            $("updated-at").textContent = `No se pudieron cargar las métricas (${err.message}).`;
        }
    }

    function schedule() {
        clearInterval(state.timer);
        state.timer = setInterval(() => { if (!document.hidden) loadMetrics(); }, REFRESH_MS);
    }

    document.querySelectorAll("[data-range]").forEach((btn) => btn.addEventListener("click", () => {
        state.range = btn.dataset.range;
        document.querySelectorAll("[data-range]").forEach((b) => {
            const on = b === btn;
            b.classList.toggle("active", on);
            b.setAttribute("aria-pressed", on ? "true" : "false");
        });
        loadMetrics();
    }));
    $("btn-refresh").addEventListener("click", loadMetrics);
    $("btn-new-connection")?.addEventListener("click", () => openConnectionModal(null));
    // Al crear, elegir proveedor completa los valores sugeridos; al editar solo cambia el proveedor
    $("f-provider").addEventListener("change", (ev) => {
        const editing = form.action.endsWith("/update");
        if (editing) {
            const p = (CONFIG.presets || []).find((x) => x.id === ev.target.value);
            $("preset-notes").textContent = p ? p.notes || "" : "";
        } else {
            applyPreset(ev.target.value);
        }
    });
    $("conn-search").addEventListener("input", () => state.data && renderConnections(state.data));
    $("conn-status").addEventListener("change", () => state.data && renderConnections(state.data));
    document.addEventListener("visibilitychange", () => { if (!document.hidden) loadMetrics(); });
    window.addEventListener("scroll", hideTooltip, { passive: true });
    let resizeTimer = null;
    window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(render, 150); });

    // Abre la pestaña indicada en el hash (#conexiones, #producto, #diagramas) y lo mantiene al navegar
    const hashTab = { "#conexiones": "tab-conexiones", "#nueva-conexion": "tab-conexiones", "#producto": "tab-producto", "#usuarios": "tab-usuarios", "#diagramas": "tab-diagramas" }[location.hash];
    if (hashTab && $(hashTab)) bootstrap.Tab.getOrCreateInstance($(hashTab)).show();
    // Enlace directo para abrir el alta de una conexión
    if (location.hash === "#nueva-conexion" && CAN_EDIT) openConnectionModal(null);
    document.querySelectorAll('[data-bs-toggle="tab"]').forEach((t) => t.addEventListener("shown.bs.tab", () => {
        history.replaceState(null, "", t.id === "tab-resumen" ? location.pathname : `#${t.id.replace("tab-", "")}`);
        hideTooltip();
        render();
    }));

    window.addEventListener("hashchange", () => {
        const tab = { "#conexiones": "tab-conexiones", "#producto": "tab-producto", "#usuarios": "tab-usuarios", "#diagramas": "tab-diagramas", "": "tab-resumen" }[location.hash];
        if (tab && $(tab)) bootstrap.Tab.getOrCreateInstance($(tab)).show();
    });

    loadMetrics();
    schedule();
    if (CAN_EDIT && $("members-body")) loadMembers();
})();
