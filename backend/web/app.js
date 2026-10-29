"use strict";

// ---------------------------------------------------------------------------
// Estado y configuración
// ---------------------------------------------------------------------------

const API_PREFIX = "/api/v1";
const MAX_FILE_BYTES = 10 * 1024 * 1024;
// Formatos que acepta la subida de archivos (Issues #47 y #62), con su Content-Type.
const FILE_TYPES = {
    pdf: { label: "PDF", mime: "application/pdf" },
    docx: { label: "Word", mime: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" },
    pptx: { label: "PowerPoint", mime: "application/vnd.openxmlformats-officedocument.presentationml.presentation" },
};
const MIN_TEXT_CHARS = 10;
const MAX_TEXT_CHARS = 20000;
const REQUEST_TIMEOUT_MS = 180000; // la IA real puede tardar; Railway corta antes si algo se cuelga
const MAX_LOG_ENTRIES = 30;

const ADAPTATION_TYPES = {
    lenguaje_claro: "Lenguaje claro",
    consigna_en_pasos: "Consigna en pasos",
    reorganizar: "Reorganizar en bloques",
    mejorar_estructura: "Mejorar estructura",
};

const TRANSFORMATION_FORMATS = {
    no_images: "Sin imágenes",
    mind_map: "Mapa mental",
    infographic: "Infografía",
    audio: "Audio",
};
// Exportaciones del resultado aceptado por formato (GET /transformations/{id}/export)
const TRANSFORMATION_EXPORTS = { no_images: ["docx", "pdf", "md", "txt"], mind_map: ["md", "txt"], audio: ["md", "txt"] };
const VIEW_LABELS = {
    markdown: "Lista anidada (Markdown, accesible con lector de pantalla)",
    mermaid: "Mermaid (mindmap)",
    transcript: "Transcripción (el guion, en Markdown)",
};
const TRANSFORMATION_POLL_MS = 1500;
const TRANSFORMATION_POLL_LIMIT_MS = 180000;
// El audio (#122) se genera por fragmentos después de aceptar el guion: puede tardar varios minutos
const AUDIO_POLL_LIMIT_MS = 900000;

const SAMPLE_TEXT = `Unidad 3: El ciclo del agua

El ciclo del agua, también conocido como ciclo hidrológico, es el proceso de circulación del agua entre los distintos compartimentos de la hidrosfera, en el cual se producen transformaciones de estado físico que dependen de la temperatura y la presión, y que permiten que el agua pase de la superficie terrestre a la atmósfera mediante la evaporación y la transpiración de las plantas, para luego condensarse formando nubes y regresar a la superficie en forma de precipitación.

Actividad 1: Leé el texto de la página 8, subrayá las ideas principales, armá un cuadro comparativo entre evaporación y condensación, respondé las preguntas del final y entregá todo en una hoja aparte antes del viernes.

Observá la imagen de la derecha y completá el esquema.`;

const state = {
    apiBase: "",
    analysis: null, // último AccessibilityAnalysisResponse
    proposals: [],
    finalVersion: null, // último AdaptedDocumentResponse del diagnóstico actual
    report: null, // último FinalReportResponse del diagnóstico actual
    upload: null, // último archivo subido al almacenamiento: { key, fileId, expiresAt }
    files: new Map(), // file_id → { info, links: Map(share_id → url) } de la sección 9
    transformations: new Map(), // transformation_id → TransformationResponse de la sección 10
};

const $ = (id) => document.getElementById(id);

function storageGet(key) {
    try { return localStorage.getItem(key); } catch { return null; }
}

function storageSet(key, value) {
    try { localStorage.setItem(key, value); } catch { /* modo privado o storage bloqueado */ }
}

// ---------------------------------------------------------------------------
// Helpers de DOM (todo el contenido dinámico va por textContent: nada de innerHTML)
// ---------------------------------------------------------------------------

function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
        if (value === undefined || value === null || value === false) continue;
        if (key === "class") node.className = value;
        else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
        else if (key in node && key !== "list") node[key] = value;
        else node.setAttribute(key, value);
    }
    for (const child of children.flat()) {
        if (child === null || child === undefined || child === false) continue;
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
}

// Reemplaza el contenido de un nodo ignorando null/false (replaceChildren los escribiría como texto).
function fill(node, ...children) {
    node.replaceChildren(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
}

function badge(text, variant) {
    return el("span", { class: `badge b-${variant || text}` }, text);
}

function kv(pairs) {
    const dl = el("dl", { class: "kv" });
    for (const [key, value] of pairs) {
        if (value === undefined) continue;
        dl.append(el("dt", {}, key), el("dd", {}, value === null ? "—" : String(value)));
    }
    return dl;
}

function rawJson(data, label = "Ver JSON completo") {
    return el("details", {}, el("summary", {}, label), el("pre", {}, JSON.stringify(data, null, 2)));
}

function errorBox(message) {
    return el("div", { class: "error" }, message);
}

function fmtDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : `${d.toLocaleString()} (${iso})`;
}

function fmtBytes(n) {
    return n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(2)} MB` : `${Math.ceil(n / 1024)} KB`;
}

// ---------------------------------------------------------------------------
// Cliente HTTP con registro de requests
// ---------------------------------------------------------------------------

// Validación del formulario: se muestra al usuario, no es un fallo de la página.
class InputError extends Error {}

class ApiError extends Error {
    constructor(message, status, data) {
        super(message);
        this.status = status;
        this.data = data;
    }
}

function formatDetail(data, status) {
    const detail = data && data.detail;
    if (typeof detail === "string") return `${status}: ${detail}`;
    if (detail && typeof detail === "object" && !Array.isArray(detail) && detail.message) return `${status}: ${detail.message}`;
    if (Array.isArray(detail)) {
        // 422 de FastAPI: lista de {loc, msg}
        const lines = detail.map((d) => `• ${(d.loc || []).join(" → ")}: ${d.msg}`);
        return `${status}: datos inválidos\n${lines.join("\n")}`;
    }
    if (typeof data === "string" && data) return `${status}: ${data.slice(0, 500)}`;
    return `${status}: error sin detalle`;
}

function buildUrl(path, query = {}) {
    let url;
    try {
        url = new URL(state.apiBase + API_PREFIX + path);
    } catch {
        throw new ApiError(`URL del backend inválida: "${state.apiBase}". Ejemplo: https://mi-backend.up.railway.app`, 0, null);
    }
    for (const [key, value] of Object.entries(query)) {
        if (value === undefined || value === null) continue;
        // Las listas se repiten como parámetro: ?dismissed=a&dismissed=b
        if (Array.isArray(value)) value.forEach((v) => url.searchParams.append(key, v));
        else url.searchParams.set(key, value);
    }
    return url.toString();
}

function shellQuote(s) {
    return `'${String(s).replace(/'/g, `'\\''`)}'`;
}

function toCurl(method, url, body) {
    const parts = [`curl -X ${method} ${shellQuote(url)}`];
    if (body && body.kind === "json") {
        parts.push(`-H 'Content-Type: application/json'`, `-d ${shellQuote(JSON.stringify(body.value))}`);
    } else if (body && body.kind === "file") {
        const type = (FILE_TYPES[fileExtension(body.value.name)] || FILE_TYPES.pdf).mime;
        parts.push(`-F ${shellQuote(`file=@${body.value.name};type=${type}`)}`);
    } else if (body && body.kind === "binary") {
        parts.push(`-H ${shellQuote(`Content-Type: ${body.type}`)}`, `--data-binary ${shellQuote(`@${body.value.name}`)}`);
    }
    return parts.join(" \\\n  ");
}

/**
 * Llama a la API y registra la llamada. `body` es {kind: "json", value} o {kind: "file", value: File}.
 * Lanza ApiError con un mensaje legible si la respuesta no es 2xx o no hay conexión.
 */
async function api(method, path, { query, body, busyText } = {}) {
    if (!state.apiBase) throw new ApiError("Configurá la URL del backend en el paso 0.", 0, null);

    const url = buildUrl(path, query);
    const init = { method, headers: { Accept: "application/json" } };
    const authToken = window.TestWebAuth && TestWebAuth.token();
    if (authToken) init.headers.Authorization = `Bearer ${authToken}`;
    if (body && body.kind === "json") {
        init.headers["Content-Type"] = "application/json";
        init.body = JSON.stringify(body.value);
    } else if (body && body.kind === "file") {
        const form = new FormData();
        form.append("file", body.value, body.value.name);
        init.body = form;
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    init.signal = controller.signal;

    setBusy(true, busyText);
    const started = performance.now();
    let status = 0;
    let data = null;
    let failure = null;
    try {
        const res = await fetch(url, init);
        status = res.status;
        const text = await res.text();
        try { data = text ? JSON.parse(text) : null; } catch { data = text; }
        if (!res.ok) failure = new ApiError(formatDetail(data, status), status, data);
    } catch (err) {
        failure = err.name === "AbortError"
            ? new ApiError(`Sin respuesta tras ${REQUEST_TIMEOUT_MS / 1000} s (timeout del navegador).`, 0, null)
            : new ApiError(
                "No se pudo conectar con el backend. Revisá la URL, que el servicio esté arriba y que el " +
                "origen de esta web esté permitido en CORS (TEST_WEB_URL o BACKEND_CORS_ORIGINS en el backend).",
                0, null,
            );
    } finally {
        clearTimeout(timer);
        setBusy(false);
    }

    addLogEntry({
        method, url, status, data, body,
        ms: Math.round(performance.now() - started),
        error: failure ? failure.message : null,
    });
    // Sesión vencida o acceso revocado: vuelve a la pantalla de login
    if (status === 401 && data && data.code === "test_web_login_required" && window.TestWebAuth) TestWebAuth.expired();
    if (failure) throw failure;
    return data;
}

function setBusy(on, text) {
    $("busy").hidden = !on;
    $("busy-text").textContent = text || "Procesando…";
    for (const button of document.querySelectorAll("button")) button.disabled = on;
}

function addLogEntry({ method, url, status, data, body, ms, error }) {
    const container = $("log-entries");
    if (!container.querySelector("details")) container.replaceChildren();

    // Las subidas (PUT a la URL firmada) se resumen sin la firma; el curl la conserva para reproducir
    const shown = method === "PUT" ? `${url.split("?")[0]}?…` : url;
    const path = shown.startsWith(state.apiBase) ? shown.replace(state.apiBase, "") : shown;
    const statusBadge = badge(status ? String(status) : "sin respuesta", status >= 200 && status < 300 ? "ok" : "error");
    let requestBody = null;
    if (body && body.kind === "json") requestBody = JSON.stringify(body.value, null, 2);
    if (body && body.kind === "file") requestBody = `multipart/form-data → file: ${body.value.name} (${fmtBytes(body.value.size)})`;
    if (body && body.kind === "binary") requestBody = `${body.type} → ${body.value.name} (${fmtBytes(body.value.size)}), directo al almacenamiento`;

    const entry = el("details", { class: "card" },
        el("summary", {}, statusBadge, `${method} ${path} · ${ms} ms · ${new Date().toLocaleTimeString()}`),
        error ? errorBox(error) : null,
        requestBody ? el("div", {}, el("h3", {}, "Request"), el("pre", {}, requestBody)) : null,
        el("h3", {}, "Response"),
        el("pre", {}, typeof data === "string" ? data : JSON.stringify(data, null, 2)),
        el("h3", {}, "curl"),
        el("pre", {}, toCurl(method, url, body)),
    );
    container.prepend(entry);
    while (container.children.length > MAX_LOG_ENTRIES) container.lastChild.remove();
}

/**
 * Ejecuta una acción y muestra su error en el contenedor indicado (sin borrar lo que ya había).
 * `action` recibe los ids de datos personales que el docente ya descartó: si el backend responde
 * 422 sensitive_data_detected (Issue #66), se ofrece descartar hallazgos y reintentar.
 */
async function run(target, action, dismissed = []) {
    target.querySelectorAll(":scope > .error, :scope > .notice").forEach((n) => n.remove());
    try {
        await action(dismissed);
    } catch (err) {
        const box = isSensitiveData(err)
            ? sensitiveDataPanel(err.data.detail, (ids) => run(target, action, [...dismissed, ...ids]))
            : errorBox(err.message || String(err));
        const title = target.querySelector(":scope > h2");
        if (title) title.after(box);
        else target.prepend(box);
        if (!(err instanceof ApiError || err instanceof InputError)) console.error(err);
    }
}

function isSensitiveData(err) {
    return err instanceof ApiError && err.status === 422
        && err.data && err.data.detail && err.data.detail.code === "sensitive_data_detected";
}

function sensitiveDataPanel(detail, retry) {
    const checks = (detail.findings || []).map((f) => ({ id: f.id, input: el("input", { type: "checkbox" }), f }));
    const panel = el("div", { class: "notice" },
        el("strong", {}, `Posibles datos personales (${detail.total ?? checks.length})`),
        el("p", {}, detail.message || "El material contiene posibles datos personales: no se envió a la IA ni se guardó."),
        el("p", { class: "muted" }, "Si alguno no es un dato personal, marcalo y reintentá. Para continuar hay que descartar todos; si son datos reales, quitalos del material."),
        checks.map(({ input, f }) => el("label", { class: "check" }, input,
            `${f.label} · línea ${f.line} · `, el("code", {}, f.masked), ` (${f.id})`)),
    );
    panel.append(el("div", { class: "row" },
        el("button", {
            type: "button", class: "secondary",
            onclick: () => checks.forEach((c) => { c.input.checked = true; }),
        }, "Marcar todos"),
        el("button", {
            type: "button",
            onclick: () => {
                const ids = checks.filter((c) => c.input.checked).map((c) => c.id);
                if (!ids.length) {
                    panel.querySelectorAll(".error").forEach((n) => n.remove());
                    panel.append(errorBox("Marcá al menos un hallazgo que no sea un dato personal."));
                    return;
                }
                retry(ids);
            },
        }, "Descartar marcados y reintentar"),
    ));
    return panel;
}

// ---------------------------------------------------------------------------
// 0. Backend
// ---------------------------------------------------------------------------

function normalizeApiBase(raw) {
    let value = (raw || "").trim().replace(/\/+$/, "");
    if (value.endsWith(API_PREFIX)) value = value.slice(0, -API_PREFIX.length);
    return value;
}

function setApiBase(raw) {
    state.apiBase = normalizeApiBase(raw);
    $("api-url").value = state.apiBase;
    storageSet("innovalab.apiBase", state.apiBase);
}

function isMock() {
    return $("mock").checked;
}

async function checkHealth() {
    const out = $("health-status");
    out.className = "muted";
    out.textContent = "";
    try {
        const data = await api("GET", "/health", { busyText: "Probando conexión (si el backend estaba dormido puede tardar)…" });
        out.className = "ok";
        out.textContent = `Conectado · ${JSON.stringify(data)}`;
    } catch (err) {
        out.className = "error";
        out.textContent = err.message;
    }
}

// ---------------------------------------------------------------------------
// 1. Material
// ---------------------------------------------------------------------------

function switchTab(name) {
    for (const tab of document.querySelectorAll(".tab")) tab.classList.toggle("active", tab.dataset.tab === name);
    $("tab-text").hidden = name !== "text";
    $("tab-file").hidden = name !== "file";
}

function updateTextCount() {
    const n = $("text-input").value.length;
    const out = $("text-count");
    out.textContent = `${n.toLocaleString()} caracteres`;
    out.className = n > 0 && (n < MIN_TEXT_CHARS || n > MAX_TEXT_CHARS) ? "error" : "muted";
}

function readText() {
    const text = $("text-input").value;
    if (text.trim().length < MIN_TEXT_CHARS) throw new InputError(`El texto debe tener al menos ${MIN_TEXT_CHARS} caracteres.`);
    if (text.length > MAX_TEXT_CHARS) throw new InputError(`El texto supera el máximo de ${MAX_TEXT_CHARS.toLocaleString()} caracteres.`);
    return text;
}

function fileExtension(name) {
    const dot = name.lastIndexOf(".");
    return dot === -1 ? "" : name.slice(dot + 1).toLowerCase();
}

function readFile() {
    const file = $("file-input").files[0];
    if (!file) throw new InputError("Elegí un archivo PDF, Word o PowerPoint.");
    if (!FILE_TYPES[fileExtension(file.name)]) throw new InputError("El archivo debe tener extensión .pdf, .docx o .pptx.");
    if (file.size > MAX_FILE_BYTES) throw new InputError(`El archivo pesa ${fmtBytes(file.size)}; el máximo es 10 MB.`);
    return file;
}

function updateFileInfo() {
    const file = $("file-input").files[0];
    const type = file && FILE_TYPES[fileExtension(file.name)];
    const uploaded = file && state.upload && state.upload.key === fileKey(file);
    $("file-info").textContent = file
        ? `${file.name} · ${type ? type.label : "formato no admitido"} · ${fmtBytes(file.size)}${uploaded ? " · ya subido" : " · se sube al extraer o analizar"}`
        : "";
}

function renderExtraction(doc) {
    const box = $("extraction");
    box.hidden = false;
    const s = doc.structure || {};
    fill(box,
        el("h3", {}, "Extracción (sin IA)"),
        kv([
            ["Origen", doc.source_type],
            ["Archivo", doc.filename],
            [doc.source_type === "pptx" ? "Diapositivas" : "Páginas", doc.page_count],
            ["Palabras", doc.word_count],
            ["Caracteres", doc.character_count],
            ["Imágenes", doc.image_count],
            ["Con texto alternativo", doc.source_type === "docx" || doc.source_type === "pptx" ? doc.images_with_alt_text : undefined],
            ["PDF escaneado", doc.source_type === "pdf" ? (doc.is_scanned_pdf ? "sí" : "no") : undefined],
            ["Títulos detectados", (s.headings || []).length],
            ["Niveles de jerarquía", s.hierarchy_levels],
            ["Ítems de lista", s.list_items_count],
            ["Se trunca para la IA", doc.is_truncated ? "sí" : "no"],
        ]),
        doc.warnings && doc.warnings.length
            ? el("div", {}, el("h3", {}, "Advertencias"), el("ul", {}, doc.warnings.map((w) => el("li", {}, w))))
            : null,
        (s.headings || []).length
            ? el("details", {}, el("summary", {}, "Títulos detectados"),
                el("ul", {}, s.headings.map((h) => el("li", {},
                    doc.source_type === "docx"
                        ? `H${h.level} · ${h.text}`
                        : `H${h.level} · ${doc.source_type === "pptx" ? "diap." : "pág."} ${h.page_number} · ${h.text}`))))
            : null,
        el("details", {}, el("summary", {}, "Texto extraído"), el("pre", {}, doc.raw_text || "")),
        rawJson(doc),
    );
}

async function processText() {
    const text = readText();
    renderExtraction(await api("POST", "/documents/process-text", { body: { kind: "json", value: { text } } }));
}

// ---------------------------------------------------------------------------
// Subida directa al almacenamiento (Issues #62 y #63): el archivo no pasa por la API
// ---------------------------------------------------------------------------

const fileKey = (file) => `${file.name}|${file.size}|${file.lastModified}`;

// PUT con XMLHttpRequest para mostrar el progreso (fetch no lo informa en las subidas)
function putFile(url, headers, file) {
    return new Promise((resolve) => {
        const xhr = new XMLHttpRequest();
        xhr.open("PUT", url);
        for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v);
        xhr.upload.onprogress = (e) => {
            if (e.lengthComputable) $("busy-text").textContent = `Subiendo ${file.name}… ${Math.round((e.loaded / e.total) * 100)} %`;
        };
        xhr.onload = () => resolve({ status: xhr.status, text: xhr.responseText });
        xhr.onerror = () => resolve({ status: 0, text: "" });
        xhr.ontimeout = () => resolve({ status: 0, text: "timeout" });
        xhr.timeout = REQUEST_TIMEOUT_MS;
        xhr.send(file);
    });
}

/**
 * Sube el archivo elegido y devuelve su file_id. Si es el mismo archivo que ya se subió (y no
 * venció), lo reutiliza: extraer y diagnosticar no lo vuelven a subir.
 */
async function ensureUploaded() {
    const file = readFile();
    const key = fileKey(file);
    if (state.upload && state.upload.key === key && new Date(state.upload.expiresAt) > new Date()) return state.upload.fileId;

    const type = FILE_TYPES[fileExtension(file.name)].mime;
    const ticket = await api("POST", "/files/upload-url", {
        body: { kind: "json", value: { filename: file.name, content_type: type, size_bytes: file.size } },
        busyText: "Pidiendo la URL de subida…",
    });

    setBusy(true, `Subiendo ${file.name}…`);
    const started = performance.now();
    const put = await putFile(ticket.upload.url, ticket.upload.headers, file);
    setBusy(false);
    const ok = put.status >= 200 && put.status < 300;
    const error = ok ? null : put.status === 0
        ? "No se pudo subir el archivo: sin conexión con el almacenamiento o el origen de esta web no está en su CORS."
        : `El almacenamiento rechazó la subida (${put.status}). Si la URL venció (5 minutos), volvé a intentar.`;
    addLogEntry({
        method: "PUT", url: ticket.upload.url, status: put.status, data: put.text || null,
        body: { kind: "binary", value: file, type }, ms: Math.round(performance.now() - started), error,
    });
    if (!ok) throw new ApiError(error, put.status, null);

    const info = await api("POST", `/files/${encodeURIComponent(ticket.file.file_id)}/complete`, { busyText: "Verificando el archivo…" });
    state.upload = { key, fileId: info.file_id, expiresAt: info.expires_at };
    rememberFile(info);
    $("file-info").textContent = `${file.name} · subido (file_id ${info.file_id}, vence ${fmtDate(info.expires_at)})`;
    return info.file_id;
}

async function processFile() {
    const fileId = await ensureUploaded();
    const label = FILE_TYPES[fileExtension(readFile().name)].label;
    renderExtraction(await api("POST", `/documents/files/${encodeURIComponent(fileId)}`, { busyText: `Extrayendo ${label}…` }));
}

const ANALYSIS_BUSY = () => isMock()
    ? "Analizando (mock)…"
    : "Analizando con IA real (puede tardar hasta 1 minuto)…";

async function analyzeText(dismissed = []) {
    const text = readText();
    const data = await api("POST", "/analysis/sample", {
        query: { mock: isMock(), dismissed },
        body: { kind: "json", value: { text } },
        busyText: ANALYSIS_BUSY(),
    });
    await showAnalysis(data);
}

async function analyzeFile(dismissed = []) {
    const fileId = await ensureUploaded();
    const data = await api("POST", `/analysis/files/${encodeURIComponent(fileId)}`, {
        query: {
            mock: isMock(),
            dismissed,
            confirm_images_without_personal_data: $("confirm-images").checked || undefined,
        },
        busyText: ANALYSIS_BUSY(),
    });
    await showAnalysis(data);
}

// ---------------------------------------------------------------------------
// 2. Diagnóstico
// ---------------------------------------------------------------------------

async function loadAnalysis() {
    const id = $("analysis-id-input").value.trim();
    if (!id) throw new InputError("Ingresá un analysis_id.");
    await showAnalysis(await api("GET", `/analysis/${encodeURIComponent(id)}`));
}

async function showAnalysis(data) {
    state.analysis = data;
    state.proposals = [];
    state.finalVersion = null;
    state.report = null;
    fill($("final-version"), el("p", { class: "muted" }, "Todavía no se generó."));
    fill($("report"), el("p", { class: "muted" }, "Todavía no se generó."));
    if (data.analysis_id) $("analysis-id-input").value = data.analysis_id;
    renderDiagnosis(data);
    renderBarriers();
    $("diagnosis").scrollIntoView({ behavior: "smooth", block: "start" });
    if (data.analysis_id) {
        await refreshProposals();
        await refreshDecisions();
    } else {
        $("proposals").replaceChildren(errorBox("El diagnóstico no trae analysis_id: no se pueden pedir adaptaciones."));
    }
}

function renderDiagnosis(d) {
    const m = d.metadata || {};
    const sb = d.score_breakdown;
    const box = $("diagnosis");
    box.parentElement.querySelectorAll(":scope > .error").forEach((n) => n.remove());

    fill(box,
        el("div", { class: "cols" },
            el("div", {},
                el("div", { class: "score" }, `${d.estimated_score} / 100`),
                el("p", {}, d.summary),
            ),
            kv([
                ["analysis_id", d.analysis_id],
                ["Vence", fmtDate(d.expires_at)],
                ["Proveedor", `${m.provider} · ${m.model}`],
                ["Conexión", m.connection],
                ["Latencia", m.latency_ms !== undefined ? `${m.latency_ms} ms` : undefined],
                ["Tokens", m.total_tokens ?? "—"],
                ["Origen", m.source_type + (m.filename ? ` · ${m.filename}` : "")],
                [m.source_type === "pptx" ? "Diapositivas / imágenes" : "Páginas / imágenes", `${m.page_count} / ${m.image_count}`],
                ["Palabras analizadas", `${m.analyzed_word_count}${m.is_truncated ? " (truncado)" : ""}`],
                ["Fragmentos no verificados", m.unverified_fragments],
            ]),
        ),

        el("h3", {}, "Dimensiones"),
        el("table", {},
            el("tr", {}, el("th", {}, "Dimensión"), el("th", {}, "Estado"), el("th", {}, "Comentario")),
            (d.dimensions || []).map((dim) =>
                el("tr", {}, el("td", {}, dim.category), el("td", {}, badge(dim.status)), el("td", {}, dim.comment))),
        ),

        sb ? el("div", {},
            el("h3", {}, "Cálculo del puntaje"),
            kv([
                ["IA", sb.ai_score === null ? "no consultada" : `${sb.ai_score} (peso ${sb.ai_weight})`],
                ["Reglas", `${sb.rules_score} (peso ${sb.rules_weight})`],
                ["Tope", sb.cap === null || sb.cap === undefined ? "—" : `${sb.cap}: ${sb.cap_reason || ""}`],
                ["Final", sb.final_score],
            ]),
            sb.penalties && sb.penalties.length ? el("table", {},
                el("tr", {}, el("th", {}, "Regla"), el("th", {}, "Categoría"), el("th", {}, "Casos"), el("th", {}, "Puntos")),
                sb.penalties.map((p) => el("tr", {},
                    el("td", {}, p.rule), el("td", {}, p.category), el("td", {}, p.count), el("td", {}, `-${p.points}`))),
            ) : null,
        ) : null,

        d.strengths && d.strengths.length
            ? el("div", {}, el("h3", {}, "Fortalezas"), el("ul", {}, d.strengths.map((s) => el("li", {}, s))))
            : null,

        d.warnings && d.warnings.length
            ? el("div", {}, el("h3", {}, "Advertencias"), el("ul", {}, d.warnings.map((w) => el("li", {}, w))))
            : null,

        d.alt_text_proposals && d.alt_text_proposals.length ? el("div", {},
            el("h3", {}, `Texto alternativo propuesto (${d.alt_text_proposals.length})`),
            el("table", {},
                el("tr", {}, el("th", {}, "#"), el("th", {}, "Ubicación"), el("th", {}, "Alt text"), el("th", {}, "Propósito")),
                d.alt_text_proposals.map((a) => el("tr", {},
                    el("td", {}, a.image_index),
                    el("td", {}, a.location_description),
                    el("td", {}, a.suggested_alt_text),
                    el("td", {}, a.pedagogical_purpose || "—"))),
            ),
        ) : null,

        m.rules_metrics ? rawJson(m.rules_metrics, "Métricas del motor de reglas") : null,
        rawJson(d),
    );
}

// ---------------------------------------------------------------------------
// 3. Barreras → propuestas
// ---------------------------------------------------------------------------

function suggestType(barrier) {
    const text = `${barrier.issue} ${barrier.recommendation}`.toLowerCase();
    if (text.includes("consigna") || text.includes("acciones")) return "consigna_en_pasos";
    if (barrier.category === "estructura") return text.includes("párrafo") ? "reorganizar" : "mejorar_estructura";
    return "lenguaje_claro";
}

// Estado de una barrera según sus propuestas (Issue #94). Como mucho hay una pendiente por barrera.
function barrierState(barrierId) {
    const own = state.proposals.filter((p) => p.barrier_id === barrierId);
    const pending = own.find((p) => p.status === "pendiente");
    if (pending) return { key: "pendiente", label: "propuesta pendiente", proposal: pending };
    const applied = own.find((p) => p.status === "editada") || own.find((p) => p.status === "aceptada");
    if (applied) return { key: "aceptada", label: `resuelta (${applied.status})`, proposal: applied };
    if (own.some((p) => p.status === "descartada")) return { key: "descartada", label: "descartada" };
    return { key: "none", label: "sin propuesta" };
}

const isDone = (s) => s.key === "aceptada" || s.key === "descartada";

// Las barreras que citan el mismo fragmento se muestran juntas (son problemas distintos del mismo texto).
function quoteKey(b) {
    if (!b.original_text) return `global:${b.id}`;
    return b.original_text.normalize("NFKC").toLowerCase().replace(/["'“”‘’«»]/g, "").replace(/\s+/g, " ").trim();
}

function renderBarriers() {
    const box = $("barriers");
    const barriers = (state.analysis && state.analysis.barriers) || [];
    if (!barriers.length) {
        box.replaceChildren(el("p", { class: "muted" }, state.analysis ? "El diagnóstico no detectó barreras." : "Sin barreras para mostrar."));
        return;
    }
    const groups = new Map();
    for (const b of barriers) {
        const key = quoteKey(b);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(b);
    }
    const showDone = $("show-done-barriers").checked;
    const counts = {};
    let hidden = 0;
    const cards = [];
    for (const group of groups.values()) {
        const states = group.map((b) => barrierState(b.id));
        states.forEach((s) => { counts[s.label.split(" (")[0]] = (counts[s.label.split(" (")[0]] || 0) + 1; });
        if (states.every(isDone) && !showDone) {
            hidden += group.length;
            continue;
        }
        cards.push(barrierGroupCard(group, states));
    }
    fill(box,
        el("p", {}, Object.entries(counts).map(([label, n]) => badge(`${label}: ${n}`, label === "sin propuesta" ? "id" : {
            "propuesta pendiente": "pendiente", resuelta: "aceptada", descartada: "descartada",
        }[label]))),
        cards,
        hidden ? el("p", { class: "muted" }, `${hidden} barrera(s) resuelta(s) o descartada(s) ocultas. Marcá "Mostrar resueltas y descartadas" para verlas.`) : null,
        !cards.length && !hidden ? el("p", { class: "muted" }, "Sin barreras para mostrar.") : null,
    );
}

function barrierGroupCard(group, states) {
    const first = group[0];
    return el("div", { class: "card" },
        group.length > 1 ? el("p", { class: "muted" }, `${group.length} problemas sobre el mismo fragmento:`) : null,
        first.original_text ? el("pre", {}, first.original_text) : null,
        group.map((b, i) => barrierItem(b, states[i], group.length > 1)),
    );
}

function scrollToProposal(proposalId) {
    const target = document.getElementById(`proposal-${proposalId}`);
    if (target) {
        target.scrollIntoView({ behavior: "smooth", block: "start" });
        target.classList.add("flash");
        setTimeout(() => target.classList.remove("flash"), 1500);
    }
}

function barrierItem(b, st, grouped) {
    const item = el("div", { class: grouped ? "barrier-item" : "" });
    // append escribiría null como texto: se filtra igual que en fill()
    const add = (...children) => item.append(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
    add(
        el("div", {},
            badge(b.id, "id"), badge(b.severity), badge(b.category, "cat"), badge(b.source === "ai" ? "IA" : "reglas", "src"),
            badge(st.label, st.key === "none" ? "id" : st.key),
            b.fragment_found === false ? badge("fragmento no encontrado en el material", "error") : null,
            el("strong", {}, b.issue),
        ),
        b.original_text ? null : el("p", { class: "muted" }, "(barrera global, sin fragmento citado)"),
        kv([
            ["Por qué", b.explanation],
            ["Recomendación", b.recommendation],
            ["Reescritura sugerida", b.suggested_rewrite || undefined],
        ]),
    );
    if (st.proposal) {
        add(el("div", { class: "row" },
            el("span", { class: "muted grow" }, st.key === "pendiente"
                ? "Tiene una propuesta pendiente: decidila en el paso 4 antes de pedir otra."
                : "Ya tiene una propuesta aplicada. Para cambiarla, deshacé la decisión en el paso 4."),
            el("button", { type: "button", class: "secondary", onclick: () => scrollToProposal(st.proposal.proposal_id) }, "Ver propuesta"),
        ));
        return item;
    }
    if (st.key === "descartada") add(el("p", { class: "muted" }, "La propuesta anterior se descartó: podés pedir otra."));
    add(proposalForm(b, item));
    return item;
}

function proposalForm(b, item) {
    const select = el("select", {},
        Object.entries(ADAPTATION_TYPES).map(([value, label]) => el("option", { value }, label)));
    select.value = suggestType(b);
    const fragment = el("textarea", {
        rows: 3,
        placeholder: b.original_text
            ? "Opcional: dejalo vacío para adaptar el fragmento citado por la barrera."
            : "Obligatorio: esta barrera no cita texto. Pegá el fragmento a adaptar (mín. 10 caracteres).",
    });
    const submit = el("button", {
        type: "button",
        onclick: () => run(item, async (dismissed) => {
            const payload = { analysis_id: state.analysis.analysis_id, barrier_id: b.id, adaptation_type: select.value };
            const custom = fragment.value.trim();
            if (custom) payload.fragment = custom;
            let created;
            try {
                created = await api("POST", "/adaptations", {
                    query: { mock: isMock(), dismissed },
                    body: { kind: "json", value: payload },
                    busyText: isMock() ? "Generando propuesta (mock)…" : "Generando propuesta con IA…",
                });
            } catch (err) {
                // 409: la barrera ya tiene una pendiente (por ejemplo, desde otra pestaña): se muestra esa
                if (err instanceof ApiError && err.status === 409 && err.data && err.data.detail
                    && err.data.detail.code === "pending_proposal_exists") {
                    await refreshProposals();
                    if (err.data.detail.proposal_id) scrollToProposal(err.data.detail.proposal_id);
                }
                throw err;
            }
            await refreshProposals();
            scrollToProposal(created.proposal_id);
        }),
    }, "Proponer adaptación");
    return el("div", {},
        el("div", { class: "row" }, el("label", { class: "grow" }, "Tipo de adaptación", select)),
        el("label", {}, "Fragmento a adaptar", fragment),
        el("div", { class: "row" }, submit),
    );
}

// ---------------------------------------------------------------------------
// 4. Revisión docente
// ---------------------------------------------------------------------------

function requireAnalysisId() {
    const id = state.analysis && state.analysis.analysis_id;
    if (!id) throw new InputError("Primero generá o recuperá un diagnóstico.");
    return id;
}

async function refreshProposals() {
    const analysisId = requireAnalysisId();
    state.proposals = await api("GET", "/adaptations", { query: { analysis_id: analysisId } });
    renderProposals();
}

function barrierLabel(barrierId) {
    const b = ((state.analysis && state.analysis.barriers) || []).find((x) => x.id === barrierId);
    return b ? `${barrierId} · ${b.issue}` : barrierId;
}

function renderProposals() {
    const box = $("proposals");
    renderBarriers();
    if (!state.proposals.length) {
        box.replaceChildren(el("p", { class: "muted" }, "Sin propuestas todavía. Pedí una desde una barrera (paso 3)."));
        return;
    }
    const counts = {};
    for (const p of state.proposals) counts[p.status] = (counts[p.status] || 0) + 1;
    box.replaceChildren(
        el("p", {}, Object.entries(counts).map(([status, n]) => badge(`${status}: ${n}`, status))),
        ...state.proposals.map((p) => proposalCard(p)),
    );
}

async function decide(proposalId, decision, editedText) {
    const value = { decision };
    if (decision === "editada") value.edited_text = editedText;
    await api("POST", `/adaptations/${encodeURIComponent(proposalId)}/decision`, { body: { kind: "json", value } });
    await refreshProposals();
    await refreshDecisions();
    await refreshFinalVersionIfShown();
}

async function undo(proposalId) {
    await api("POST", `/adaptations/${encodeURIComponent(proposalId)}/undo`);
    await refreshProposals();
    await refreshDecisions();
    await refreshFinalVersionIfShown();
}

function proposalActions(p, card) {
    if (p.status !== "pendiente") {
        return el("div", { class: "row" }, el("button", {
            type: "button", class: "secondary",
            onclick: () => run(card, () => undo(p.proposal_id)),
        }, "Deshacer decisión"));
    }
    const editor = el("textarea", { rows: 5, value: p.proposed_text });
    return el("div", {},
        el("div", { class: "row" },
            el("button", { type: "button", onclick: () => run(card, () => decide(p.proposal_id, "aceptada")) }, "Aceptar"),
            el("button", { type: "button", class: "danger", onclick: () => run(card, () => decide(p.proposal_id, "descartada")) }, "Descartar"),
        ),
        el("details", {},
            el("summary", {}, "Editar antes de aceptar"),
            el("label", {}, "Texto final del docente", editor),
            el("div", { class: "row" },
                el("button", {
                    type: "button",
                    onclick: () => run(card, async () => {
                        if (!editor.value.trim()) throw new InputError("El texto editado no puede estar vacío.");
                        await decide(p.proposal_id, "editada", editor.value);
                    }),
                }, "Guardar edición"),
            ),
        ),
    );
}

function proposalCard(p) {
    const card = el("div", { class: "card", id: `proposal-${p.proposal_id}` });
    fill(card,
        el("div", {},
            badge(p.status), badge(ADAPTATION_TYPES[p.adaptation_type] || p.adaptation_type, "type"),
            badge(p.fragment_source === "request" ? "fragmento del docente" : "fragmento de la barrera", "src"),
            el("strong", {}, barrierLabel(p.barrier_id)),
        ),
        el("div", { class: "cols" },
            el("div", {}, el("h3", {}, "Original"), el("pre", {}, p.original_text)),
            el("div", {}, el("h3", {}, "Propuesta"), el("pre", {}, p.proposed_text)),
        ),
        kv([
            ["Explicación", p.explanation],
            ["Objetivo conservado", p.preserved_objective],
            ["Texto final", p.final_text],
            ["Decidida", p.decided_at ? fmtDate(p.decided_at) : undefined],
            ["Modelo", p.metadata ? `${p.metadata.provider} · ${p.metadata.model} · ${p.metadata.latency_ms} ms` : undefined],
            ["proposal_id", p.proposal_id],
        ]),
        p.warnings && p.warnings.length ? el("ul", {}, p.warnings.map((w) => el("li", {}, w))) : null,
        proposalActions(p, card),
        rawJson(p),
    );
    return card;
}

// ---------------------------------------------------------------------------
// 5. Registro de decisiones
// ---------------------------------------------------------------------------

async function refreshDecisions() {
    const analysisId = requireAnalysisId();
    const entries = await api("GET", "/adaptations/decisions", { query: { analysis_id: analysisId } });
    const box = $("decisions");
    if (!entries.length) {
        box.replaceChildren(el("p", { class: "muted" }, "Sin decisiones todavía."));
        return;
    }
    box.replaceChildren(el("table", {},
        el("tr", {},
            el("th", {}, "Momento"), el("th", {}, "Barrera"), el("th", {}, "Tipo"),
            el("th", {}, "Acción"), el("th", {}, "Estado"), el("th", {}, "Texto del docente")),
        entries.map((e) => el("tr", {},
            el("td", {}, new Date(e.created_at).toLocaleString()),
            el("td", {}, e.barrier_id),
            el("td", {}, ADAPTATION_TYPES[e.adaptation_type] || e.adaptation_type),
            el("td", {}, e.action),
            el("td", {}, badge(e.from_status), "→ ", badge(e.to_status)),
            el("td", {}, e.edited_text || "—"))),
    ));
}

// ---------------------------------------------------------------------------
// 6. Versión adaptada
// ---------------------------------------------------------------------------

async function generateFinalVersion() {
    const analysisId = requireAnalysisId();
    // Las propuestas actualizadas dan el texto que se aplicó a cada una (para resaltarlo).
    state.proposals = await api("GET", "/adaptations", { query: { analysis_id: analysisId } });
    renderProposals();
    state.finalVersion = await api("GET", "/adaptations/final-version", {
        query: { analysis_id: analysisId },
        busyText: "Generando versión adaptada…",
    });
    renderFinalVersion(state.finalVersion);
}

async function refreshFinalVersionIfShown() {
    const current = (doc) => doc && state.analysis && doc.analysis_id === state.analysis.analysis_id;
    if (current(state.finalVersion)) await generateFinalVersion();
    if (current(state.report)) await generateReport();
}

// Texto con las apariciones de `needles` envueltas en <mark> (primera aparición libre de cada una).
function highlighted(text, needles, className) {
    const spans = [];
    for (const needle of needles) {
        if (!needle) continue;
        let from = 0;
        let idx;
        while ((idx = text.indexOf(needle, from)) !== -1) {
            const end = idx + needle.length;
            if (!spans.some((s) => idx < s.end && s.start < end)) {
                spans.push({ start: idx, end });
                break;
            }
            from = idx + 1;
        }
    }
    spans.sort((a, b) => a.start - b.start);
    const nodes = [];
    let cursor = 0;
    for (const s of spans) {
        if (s.start > cursor) nodes.push(text.slice(cursor, s.start));
        nodes.push(el("mark", { class: className }, text.slice(s.start, s.end)));
        cursor = s.end;
    }
    nodes.push(text.slice(cursor));
    return nodes;
}

function appliedTexts(data) {
    const byId = new Map(state.proposals.map((p) => [p.proposal_id, p]));
    return data.applied_proposals.map((id) => byId.get(id)).filter(Boolean).map((p) => ({
        original: p.original_text,
        replacement: p.status === "editada" ? (p.edited_text ?? p.final_text) : (p.proposed_text ?? p.final_text),
    }));
}

function downloadText(filename, text) {
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
    const link = el("a", { href: url, download: filename });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function renderFinalVersion(data) {
    const box = $("final-version");
    const applied = appliedTexts(data);
    const copyStatus = el("span", { class: "muted" });
    const name = (state.analysis && state.analysis.metadata && state.analysis.metadata.filename) || "material";

    fill(box,
        el("p", {},
            badge(`aplicadas: ${data.applied_count}`, "aceptada"),
            badge(`pendientes: ${data.pending_count}`, "pendiente"),
            badge(`descartadas: ${data.discarded_count}`, "descartada"),
            el("span", { class: "muted" }, `de ${data.total_proposals} propuesta(s)`),
        ),
        data.applied_count === 0
            ? el("p", { class: "muted" }, "Ningún cambio aplicado: aceptá o editá propuestas en el paso 4 y volvé a generar.")
            : null,
        data.warnings && data.warnings.length
            ? el("div", {}, el("h3", {}, "Advertencias"), el("ul", {}, data.warnings.map((w) => el("li", {}, w))))
            : null,
        el("div", { class: "cols" },
            el("div", {},
                el("h3", {}, "Original"),
                el("pre", {}, highlighted(data.original_text, applied.map((a) => a.original), "removed"))),
            el("div", {},
                el("h3", {}, "Versión adaptada"),
                el("pre", {}, highlighted(data.adapted_text, applied.map((a) => a.replacement)))),
        ),
        el("div", { class: "row" },
            el("button", {
                type: "button", class: "secondary",
                onclick: async () => {
                    try {
                        await navigator.clipboard.writeText(data.adapted_text);
                        copyStatus.textContent = "Copiado.";
                    } catch {
                        copyStatus.textContent = "El navegador no permitió copiar: seleccioná el texto a mano.";
                    }
                },
            }, "Copiar texto adaptado"),
            el("button", {
                type: "button", class: "secondary",
                onclick: () => downloadText(`${name.replace(/\.[^.]+$/, "")}-adaptado.txt`, data.adapted_text),
            }, "Descargar .txt"),
            copyStatus,
        ),
        rawJson(data),
    );
}

// ---------------------------------------------------------------------------
// 7. Informe final
// ---------------------------------------------------------------------------

async function generateReport() {
    const analysisId = requireAnalysisId();
    state.report = await api("GET", "/adaptations/report", {
        query: { analysis_id: analysisId },
        busyText: "Generando informe…",
    });
    renderReport(state.report);
}

function fmtDelta(delta) {
    return delta > 0 ? `+${delta}` : String(delta);
}

// Penalizaciones de antes y después por regla, para ver qué cambió en el puntaje de reglas.
function penaltiesComparison(before, after) {
    const rows = new Map();
    for (const p of (before && before.penalties) || []) rows.set(p.rule, { category: p.category, before: p.points, after: 0 });
    for (const p of (after && after.penalties) || []) {
        const row = rows.get(p.rule) || { category: p.category, before: 0, after: 0 };
        row.after = p.points;
        rows.set(p.rule, row);
    }
    if (!rows.size) return el("p", { class: "muted" }, "Sin penalizaciones de reglas antes ni después.");
    return el("table", {},
        el("tr", {}, el("th", {}, "Regla"), el("th", {}, "Categoría"), el("th", {}, "Antes"), el("th", {}, "Después")),
        [...rows].map(([rule, r]) => el("tr", {},
            el("td", {}, rule), el("td", {}, r.category),
            el("td", {}, r.before ? `-${r.before}` : "—"),
            el("td", { class: r.after < r.before ? "delta-up" : r.after > r.before ? "delta-down" : "" }, r.after ? `-${r.after}` : "—"))),
    );
}

function reportAsText(r) {
    const lines = [
        "Informe final de adaptación",
        `Diagnóstico: ${r.analysis_id}`,
        `Generado: ${r.generated_at}`,
        "",
        `Puntaje: ${r.score_before} → ${r.score_after} (${fmtDelta(r.score_delta)})`,
        r.disclaimer,
        "",
        "Resumen:",
        ...r.executive_summary.map((s) => `- ${s}`),
        "",
        `Barreras: ${r.total_barriers} · resueltas ${r.resolved_count} · descartadas ${r.discarded_count} · pendientes ${r.pending_count}`,
        "",
        ...r.barriers_detail.flatMap((b) => [
            `[${b.resolution_status}] ${b.barrier_id} · ${b.category} · ${b.severity} · ${b.issue}`,
            `  Original: ${b.original_text}`,
            ...(b.final_text ? [`  Final: ${b.final_text}`] : []),
        ]),
        ...(r.warnings.length ? ["", "Advertencias:", ...r.warnings.map((w) => `- ${w}`)] : []),
    ];
    return lines.join("\n");
}

function renderReport(r) {
    const box = $("report");
    const deltaClass = r.score_delta > 0 ? "delta-up" : r.score_delta < 0 ? "delta-down" : "";
    fill(box,
        el("div", { class: "cols" },
            el("div", {},
                el("div", { class: "score" }, `${r.score_before} → ${r.score_after} `,
                    el("span", { class: deltaClass }, `(${fmtDelta(r.score_delta)})`)),
                el("p", { class: "muted" }, r.disclaimer),
            ),
            kv([
                ["Barreras", r.total_barriers],
                ["Resueltas", r.resolved_count],
                ["Descartadas", r.discarded_count],
                ["Pendientes", r.pending_count],
                ["Propuestas generadas", r.total_proposals],
                ["Generado", fmtDate(r.generated_at)],
            ]),
        ),
        el("h3", {}, "Resumen"),
        el("ul", {}, r.executive_summary.map((s) => el("li", {}, s))),
        el("h3", {}, "Barreras y decisiones"),
        el("table", {},
            el("tr", {}, el("th", {}, "Barrera"), el("th", {}, "Problema"), el("th", {}, "Decisión"), el("th", {}, "Texto final")),
            r.barriers_detail.map((b) => el("tr", {},
                el("td", {}, badge(b.barrier_id, "id"), badge(b.severity), el("div", { class: "muted" }, b.category)),
                el("td", {}, b.issue, el("div", { class: "muted" }, b.original_text)),
                el("td", {}, badge(b.resolution_status)),
                el("td", {}, b.final_text || "—"))),
        ),
        el("h3", {}, "Penalizaciones de reglas"),
        penaltiesComparison(r.score_breakdown_before, r.score_breakdown_after),
        r.warnings && r.warnings.length
            ? el("div", {}, el("h3", {}, "Advertencias"), el("ul", {}, r.warnings.map((w) => el("li", {}, w))))
            : null,
        el("div", { class: "row" },
            el("button", {
                type: "button", class: "secondary",
                onclick: () => downloadText(`informe-${r.analysis_id.slice(0, 8)}.txt`, reportAsText(r)),
            }, "Descargar informe .txt"),
        ),
        rawJson(r),
    );
}

// ---------------------------------------------------------------------------
// 8. Exportar (Issues #40 y #62): el archivo queda en el almacenamiento y se baja con URL firmada
// ---------------------------------------------------------------------------

async function exportFile(kind) {
    const analysisId = requireAnalysisId();
    const format = $(`export-${kind}-format`).value;
    const data = await api("GET", `/export/${kind}`, {
        query: { analysis_id: analysisId, format, delivery: "link" },
        busyText: "Generando el archivo…",
    });
    rememberFile(data.file);
    const expires = new Date(Date.now() + data.download.expires_in_seconds * 1000);
    const box = $("exports");
    if (!box.querySelector(".card")) box.replaceChildren();
    box.prepend(el("div", { class: "card" },
        el("div", {}, badge(kind === "content" ? "material" : "informe", "type"), badge(format, "id"), el("strong", {}, data.file.filename)),
        kv([
            ["Tamaño", fmtBytes(data.file.size_bytes)],
            ["file_id", data.file.file_id],
            ["El enlace vence", expires.toLocaleTimeString()],
            ["El archivo vence", fmtDate(data.file.expires_at)],
        ]),
        el("div", { class: "row" }, el("a", { href: data.download.url, target: "_blank", rel: "noopener" }, "Descargar")),
        rawJson(data),
    ));
}

// ---------------------------------------------------------------------------
// 9. Archivos y enlaces compartidos (Issues #62 y #65)
// ---------------------------------------------------------------------------

function rememberFile(info) {
    const known = state.files.get(info.file_id);
    state.files.set(info.file_id, { info, links: known ? known.links : new Map() });
    renderFiles();
}

async function addFileById() {
    const id = $("file-id-input").value.trim();
    if (!id) throw new InputError("Ingresá un file_id.");
    rememberFile(await api("GET", `/files/${encodeURIComponent(id)}`));
    $("file-id-input").value = "";
}

async function copyToClipboard(text, button) {
    try {
        await navigator.clipboard.writeText(text);
        button.textContent = "Copiado";
    } catch {
        button.textContent = "Copialo a mano";
    }
    setTimeout(() => { button.textContent = "Copiar"; }, 1500);
}

function renderFiles() {
    const box = $("files");
    if (!state.files.size) {
        box.replaceChildren(el("p", { class: "muted" }, "Todavía no hay archivos: subí uno en el paso 1, exportá en el paso 8 o agregalo por file_id."));
        return;
    }
    box.replaceChildren(...[...state.files.values()].reverse().map((entry) => fileCard(entry)));
}

function fileCard(entry) {
    const f = entry.info;
    const card = el("div", { class: "card" });
    const sharesBox = el("div", {}, el("p", { class: "muted" }, "Enlaces: tocá \"Ver enlaces\" para consultarlos."));
    const hours = el("input", { type: "number", min: 1, placeholder: "hasta que vence el archivo", class: "hours" });

    const refresh = async () => rememberFile(await api("GET", `/files/${encodeURIComponent(f.file_id)}`));
    const download = async () => {
        const data = await api("GET", `/files/${encodeURIComponent(f.file_id)}/download-url`, { busyText: "Firmando la descarga…" });
        window.open(data.download.url, "_blank", "noopener");
    };
    const loadShares = async () => {
        const list = await api("GET", `/files/${encodeURIComponent(f.file_id)}/shares`);
        if (!list.length) {
            sharesBox.replaceChildren(el("p", { class: "muted" }, "Sin enlaces activos."));
            return;
        }
        sharesBox.replaceChildren(el("table", {},
            el("tr", {}, el("th", {}, "Enlace"), el("th", {}, "Vence"), el("th", {}, "Aperturas"), el("th", {}, "Última"), el("th", {}, "")),
            list.map((s) => {
                const url = entry.links.get(s.share_id);
                const copy = url ? el("button", { type: "button", class: "secondary", onclick: (ev) => copyToClipboard(url, ev.target) }, "Copiar") : null;
                return el("tr", {},
                    el("td", {}, url ? el("a", { href: url, target: "_blank", rel: "noopener" }, url)
                        : el("span", { class: "muted", title: s.share_id }, `${s.share_id.slice(0, 10)}… (creado antes: la URL ya no se puede ver)`)),
                    el("td", {}, fmtDate(s.expires_at)),
                    el("td", {}, String(s.access_count)),
                    el("td", {}, s.last_access_at ? fmtDate(s.last_access_at) : "nunca"),
                    el("td", {}, el("div", { class: "row" }, copy,
                        el("button", {
                            type: "button", class: "danger",
                            onclick: () => run(card, async () => {
                                await api("DELETE", `/files/${encodeURIComponent(f.file_id)}/shares/${encodeURIComponent(s.share_id)}`);
                                entry.links.delete(s.share_id);
                                await loadShares();
                            }),
                        }, "Revocar"))),
                );
            }),
        ));
    };
    const share = async () => {
        const value = hours.value ? { expires_in_hours: Number(hours.value) } : null;
        const created = await api("POST", `/files/${encodeURIComponent(f.file_id)}/shares`, value ? { body: { kind: "json", value } } : {});
        entry.links.set(created.share_id, created.url);
        await loadShares();
    };

    card.append(
        el("div", {}, badge(f.kind === "export" ? "exportación" : "subido", "type"), badge(f.status), el("strong", {}, f.filename)),
        kv([
            ["file_id", f.file_id],
            ["Tamaño", fmtBytes(f.size_bytes)],
            ["Tipo", f.content_type],
            ["Vence", fmtDate(f.expires_at)],
        ]),
        el("div", { class: "row" },
            el("button", { type: "button", class: "secondary", onclick: () => run(card, refresh) }, "Actualizar"),
            el("button", { type: "button", class: "secondary", disabled: f.status !== "uploaded", onclick: () => run(card, download) }, "Descargar"),
            el("button", { type: "button", class: "secondary", onclick: () => run(card, loadShares) }, "Ver enlaces"),
        ),
        el("div", { class: "row" },
            el("label", {}, "Vigencia del enlace (horas, opcional)", hours),
            el("button", { type: "button", disabled: f.status !== "uploaded", onclick: () => run(card, share) }, "Crear enlace para compartir"),
        ),
        el("p", { class: "muted" }, "El enlace completo se ve solo al crearlo, en esta sesión: el backend guarda un hash del token. Revocarlo lo corta al instante."),
        sharesBox,
    );
    return card;
}

// ---------------------------------------------------------------------------
// 10. Transformar material (Issue #116): pedido asíncrono, consulta hasta 'ready' y revisión
// ---------------------------------------------------------------------------

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function rememberTransformation(t) {
    state.transformations.set(t.transformation_id, t);
    renderTransformations();
}

async function createTransformation(dismissed = []) {
    const format = $("transformation-format").value;
    let source;
    if ($("transformation-source").value === "file") {
        if (!state.upload || new Date(state.upload.expiresAt) <= new Date()) {
            throw new InputError("Primero subí un archivo en el paso 1 (Procesar o Diagnosticar).");
        }
        source = { file_id: state.upload.fileId };
    } else {
        source = { analysis_id: requireAnalysisId() };
    }
    let t = await api("POST", "/transformations", {
        query: { mock: isMock(), dismissed },
        body: { kind: "json", value: { format, ...source } },
        busyText: "Pidiendo la transformación…",
    });
    rememberTransformation(t);
    await pollTransformation(t);
}

// Consulta hasta 'ready' o 'failed'. Con archivos ya registrados en /files (audio aceptado), los suma al paso 9.
async function pollTransformation(t) {
    const audioFile = t.format === "audio" && t.review_status !== "pendiente";
    const deadline = Date.now() + (audioFile ? AUDIO_POLL_LIMIT_MS : TRANSFORMATION_POLL_LIMIT_MS);
    while (!["ready", "failed"].includes(t.status) && Date.now() < deadline) {
        await sleep(TRANSFORMATION_POLL_MS);
        t = await api("GET", `/transformations/${encodeURIComponent(t.transformation_id)}`, {
            busyText: audioFile
                ? `Generando el audio${t.progress ? ` (${t.progress.done} de ${t.progress.total} fragmentos)` : ""}…`
                : `Generando ${TRANSFORMATION_FORMATS[t.format].toLowerCase()} (${t.status})…`,
        });
        rememberTransformation(t);
    }
    for (const f of [t.file, ...(t.extra_files || [])]) {
        if (f && f.file_id && !state.files.has(f.file_id)) rememberFile(await api("GET", `/files/${encodeURIComponent(f.file_id)}`));
    }
    return t;
}

async function refreshTransformations() {
    for (const t of await api("GET", "/transformations")) state.transformations.set(t.transformation_id, t);
    renderTransformations();
}

function renderTransformations() {
    const box = $("transformations");
    if (!state.transformations.size) {
        box.replaceChildren(el("p", { class: "muted" }, "Todavía no hay transformaciones."));
        return;
    }
    const items = [...state.transformations.values()].sort((a, b) => b.created_at.localeCompare(a.created_at));
    box.replaceChildren(...items.map((t) => transformationCard(t)));
}

function transformationCard(t) {
    const card = el("div", { class: "card" });
    const path = `/transformations/${encodeURIComponent(t.transformation_id)}`;
    const decide = async (decision, editedResult) => {
        const value = editedResult === undefined ? { decision } : { decision, edited_result: editedResult };
        const updated = await api("POST", `${path}/decision`, { body: { kind: "json", value }, busyText: "Guardando la decisión…" });
        rememberTransformation(updated);
        await pollTransformation(updated);  // audio: se genera después de aceptar el guion
    };
    const retryFile = async () => {
        const t2 = await api("POST", `${path}/file`, { busyText: "Reintentando el audio…" });
        rememberTransformation(t2);
        await pollTransformation(t2);
    };
    const editor = el("textarea", { rows: 10, hidden: true }, t.result ? JSON.stringify(t.result, null, 2) : "");
    const saveEdit = el("button", {
        type: "button", hidden: true,
        onclick: () => run(card, async () => {
            let edited;
            try { edited = JSON.parse(editor.value); } catch { throw new InputError("El resultado editado no es un JSON válido."); }
            await decide("editada", edited);
        }),
    }, "Guardar edición");
    const pending = t.status === "ready" && t.review_status === "pendiente";
    const accepted = t.review_status === "aceptada" || t.review_status === "editada";
    const exportAs = async (format) => {
        const data = await api("GET", `${path}/export`, { query: { format, delivery: "link" }, busyText: "Exportando…" });
        rememberFile(data.file);
        window.open(data.download.url, "_blank", "noopener");
    };

    fill(card,
        el("div", {}, badge(TRANSFORMATION_FORMATS[t.format] || t.format, "type"),
            badge(t.status, t.status === "ready" ? "ok" : t.status === "failed" ? "error" : "pendiente"),
            t.status === "ready" ? badge(t.review_status) : null,
            el("strong", {}, t.transformation_id)),
        kv([
            ["Material", `${t.source.type === "analysis" ? "diagnóstico" : "archivo"} ${t.source.id}`],
            ["Creada", fmtDate(t.created_at)],
            ["Vence", fmtDate(t.expires_at)],
            ["Modelo", t.metadata ? `${t.metadata.provider} · ${t.metadata.model} · ${t.metadata.latency_ms} ms` : undefined],
            ["file_id (aceptado)", t.file ? t.file.file_id : undefined],
            ["Capítulos (file_id)", t.extra_files && t.extra_files.length ? t.extra_files.map((f) => f.file_id).join(", ") : undefined],
            ["Progreso", t.progress ? `${t.progress.done} de ${t.progress.total} fragmentos` : undefined],
        ]),
        t.error ? errorBox(`${t.error.code}: ${t.error.message}`) : null,
        t.warnings.length ? el("ul", { class: "muted" }, t.warnings.map((w) => el("li", {}, w))) : null,
        t.file && t.file.preview_url && t.file.content_type.startsWith("audio/")
            ? el("audio", { controls: true, src: t.file.preview_url, preload: "none" }) : null,
        t.file && t.file.preview_url
            ? el("div", {}, el("a", { href: t.file.preview_url, target: "_blank", rel: "noopener" }, `Vista previa: ${t.file.filename} (${fmtBytes(t.file.size_bytes)})`)) : null,
        t.views ? Object.entries(t.views).map(([name, text]) =>
            el("details", {}, el("summary", {}, VIEW_LABELS[name] || name), el("pre", {}, text))) : null,
        t.final_result || t.result ? el("pre", {}, JSON.stringify(t.final_result || t.result, null, 2)) : null,
        el("div", { class: "row" },
            el("button", { type: "button", class: "secondary", onclick: () => run(card, async () => rememberTransformation(await api("GET", path))) }, "Actualizar"),
            pending ? el("button", { type: "button", onclick: () => run(card, () => decide("aceptada")) }, "Aceptar") : null,
            pending ? el("button", {
                type: "button", class: "secondary",
                onclick: () => { editor.hidden = !editor.hidden; saveEdit.hidden = editor.hidden; },
            }, "Editar") : null,
            pending ? el("button", { type: "button", class: "danger", onclick: () => run(card, () => decide("descartada")) }, "Descartar") : null,
            accepted && t.status === "failed" && t.format === "audio"
                ? el("button", { type: "button", onclick: () => run(card, retryFile) }, "Reintentar el audio") : null,
            accepted ? (TRANSFORMATION_EXPORTS[t.format] || []).map((format) =>
                el("button", { type: "button", class: "secondary", onclick: () => run(card, () => exportAs(format)) }, `Exportar .${format}`)) : null,
        ),
        pending ? editor : null,
        pending ? saveEdit : null,
        rawJson(t),
    );
    return card;
}

// ---------------------------------------------------------------------------
// Inicio
// ---------------------------------------------------------------------------

function init() {
    const fromQuery = new URLSearchParams(location.search).get("api");
    setApiBase(fromQuery || storageGet("innovalab.apiBase") || window.INNOVALAB_DEFAULT_API || "");
    if (fromQuery) history.replaceState(null, "", location.pathname); // limpia ?api= de la barra

    const savedMock = storageGet("innovalab.mock");
    if (savedMock !== null) $("mock").checked = savedMock === "true";

    $("api-url").addEventListener("change", (e) => { setApiBase(e.target.value); startAuth(); });
    document.addEventListener("testweb:change-backend", (e) => { setApiBase(e.detail); startAuth(); });
    $("mock").addEventListener("change", (e) => storageSet("innovalab.mock", String(e.target.checked)));
    $("btn-health").addEventListener("click", checkHealth);

    for (const tab of document.querySelectorAll(".tab")) tab.addEventListener("click", () => switchTab(tab.dataset.tab));
    $("text-input").addEventListener("input", updateTextCount);
    $("btn-sample-text").addEventListener("click", () => { $("text-input").value = SAMPLE_TEXT; updateTextCount(); });
    $("file-input").addEventListener("change", updateFileInfo);

    const material = document.querySelector("#tab-text").parentElement;
    $("btn-process-text").addEventListener("click", () => run(material, processText));
    $("btn-analyze-text").addEventListener("click", () => run(material, analyzeText));
    $("btn-process-file").addEventListener("click", () => run(material, processFile));
    $("btn-analyze-file").addEventListener("click", () => run(material, analyzeFile));

    const diagnosisSection = $("diagnosis").parentElement;
    $("btn-load-analysis").addEventListener("click", () => run(diagnosisSection, loadAnalysis));
    $("btn-refresh-proposals").addEventListener("click", () => run($("proposals").parentElement, refreshProposals));
    $("btn-refresh-decisions").addEventListener("click", () => run($("decisions").parentElement, refreshDecisions));
    $("btn-final-version").addEventListener("click", () => run($("final-version").parentElement, generateFinalVersion));
    $("btn-report").addEventListener("click", () => run($("report").parentElement, generateReport));
    $("show-done-barriers").addEventListener("change", renderBarriers);
    $("btn-export-content").addEventListener("click", () => run($("exports").parentElement, () => exportFile("content")));
    $("btn-export-report").addEventListener("click", () => run($("exports").parentElement, () => exportFile("report")));
    $("btn-add-file").addEventListener("click", () => run($("files").parentElement, addFileById));
    $("btn-transform").addEventListener("click", () => run($("transformations").parentElement, createTransformation));
    $("btn-refresh-transformations").addEventListener("click", () => run($("transformations").parentElement, refreshTransformations));
    $("btn-clear-log").addEventListener("click", () =>
        $("log-entries").replaceChildren(el("p", { class: "muted" }, "Todavía no se hizo ninguna llamada.")));

    updateTextCount();
    startAuth();
}

// Login de la web de pruebas (auth.js). Con rol de solo lectura, la IA real no está disponible.
function startAuth() {
    if (!window.TestWebAuth) return;
    TestWebAuth.start(state.apiBase, (user) => {
        const readonly = Boolean(user && !user.can_edit);
        const mock = $("mock");
        if (readonly) mock.checked = true;
        mock.disabled = readonly;
        let hint = $("readonly-hint");
        if (readonly && !hint) {
            hint = el("p", { id: "readonly-hint", class: "readonly-hint" },
                "Tu usuario es de solo lectura: podés recorrer todo el flujo en modo mock, sin consumir IA real.");
            mock.closest("label").after(hint);
        } else if (!readonly && hint) {
            hint.remove();
        }
    });
}

init();
