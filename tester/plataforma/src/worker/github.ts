// Cliente mínimo de la API de GitHub. El token (GITHUB_TOKEN) es un secreto del Worker y nunca
// llega al navegador; sin token, la plataforma funciona igual y avisa que no puede consultar GitHub.
import type { Env } from "./env";

const API = "https://api.github.com";
const TIMEOUT_MS = 10000;

export class GithubError extends Error {}

export function githubDisponible(env: Env): boolean {
    return Boolean(env.GITHUB_TOKEN && env.GITHUB_REPO);
}

export async function github<T>(env: Env, ruta: string, init: RequestInit = {}): Promise<T> {
    if (!githubDisponible(env)) throw new GithubError("GitHub no está configurado (falta GITHUB_TOKEN).");
    let res: Response;
    try {
        res = await fetch(`${API}${ruta}`, {
            ...init,
            headers: {
                Accept: "application/vnd.github+json",
                Authorization: `Bearer ${env.GITHUB_TOKEN}`,
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "innovalab-qa",
                ...(init.body ? { "Content-Type": "application/json" } : {}),
                ...init.headers,
            },
            signal: AbortSignal.timeout(TIMEOUT_MS),
        });
    } catch {
        throw new GithubError("No se pudo conectar con GitHub.");
    }
    if (!res.ok) {
        const detalle = ((await res.json().catch(() => ({}))) as { message?: string }).message;
        throw new GithubError(`GitHub respondió ${res.status}${detalle ? `: ${detalle}` : ""}.`);
    }
    return (await res.json()) as T;
}

export type IssueEncontrado = { numero: number; titulo: string; url: string; estado: string; cuerpo: string };

type IssueApi = { number: number; title: string; html_url: string; state: string; body: string | null; node_id: string };

function issueJson(i: IssueApi): IssueEncontrado {
    return { numero: i.number, titulo: i.title, url: i.html_url, estado: i.state, cuerpo: i.body ?? "" };
}

async function buscar(env: Env, consulta: string): Promise<IssueEncontrado[]> {
    const q = encodeURIComponent(`repo:${env.GITHUB_REPO} is:issue ${consulta}`);
    const r = await github<{ items: IssueApi[] }>(env, `/search/issues?q=${q}&per_page=20`);
    return r.items.map(issueJson);
}

/** Issues abiertos del repo que mencionan el caso en el título o en el cuerpo (incluye la marca oculta). */
export async function buscarIssuesDelCaso(env: Env, casoId: string): Promise<IssueEncontrado[]> {
    return buscar(env, `is:open "${casoId}" in:title,body`);
}

/** Issues (abiertos o cerrados) cuyo cuerpo contiene el texto exacto (por ejemplo una marca oculta). */
export async function buscarPorMarca(env: Env, marca: string, soloAbiertos = false): Promise<IssueEncontrado[]> {
    // La búsqueda de GitHub no indexa los comentarios HTML de forma confiable: se busca por el texto
    // de la marca y después se confirma contra el cuerpo
    const texto = marca.replace(/^<!--\s*|\s*-->$/g, "");
    const encontrados = await buscar(env, `${soloAbiertos ? "is:open " : ""}"${texto}" in:body`);
    return encontrados.filter((i) => i.cuerpo.includes(marca));
}

export type IssueCreado = { numero: number; url: string; nodeId: string };

export async function crearIssue(env: Env, datos: { titulo: string; cuerpo: string; etiquetas: string[] }): Promise<IssueCreado> {
    // Las etiquetas que no existen las crea GitHub al crear el issue
    const i = await github<IssueApi>(env, `/repos/${env.GITHUB_REPO}/issues`, {
        method: "POST",
        body: JSON.stringify({ title: datos.titulo, body: datos.cuerpo, labels: datos.etiquetas }),
    });
    return { numero: i.number, url: i.html_url, nodeId: i.node_id };
}

export async function editarIssue(env: Env, numero: number, datos: { titulo: string; cuerpo: string }): Promise<IssueCreado> {
    const i = await github<IssueApi>(env, `/repos/${env.GITHUB_REPO}/issues/${numero}`, {
        method: "PATCH",
        body: JSON.stringify({ title: datos.titulo, body: datos.cuerpo }),
    });
    return { numero: i.number, url: i.html_url, nodeId: i.node_id };
}

export async function comentarIssue(env: Env, numero: number, cuerpo: string): Promise<{ id: number; url: string }> {
    const c = await github<{ id: number; html_url: string }>(env, `/repos/${env.GITHUB_REPO}/issues/${numero}/comments`, {
        method: "POST",
        body: JSON.stringify({ body: cuerpo }),
    });
    return { id: c.id, url: c.html_url };
}

export async function graphql<T>(env: Env, query: string, variables: Record<string, unknown>): Promise<T> {
    const r = await github<{ data?: T; errors?: { message: string }[] }>(env, "/graphql", { method: "POST", body: JSON.stringify({ query, variables }) });
    if (r.errors?.length || !r.data) throw new GithubError(`GitHub (GraphQL): ${r.errors?.map((e) => e.message).join("; ") ?? "sin datos"}.`);
    return r.data;
}

/** ¿El tablero está configurado y es de la misma organización que el repo? (en local, con un repo personal, no se toca el tablero) */
export function tableroAplica(env: Env): boolean {
    const dueno = env.GITHUB_REPO?.split("/")[0]?.toLowerCase();
    return Boolean(env.GITHUB_PROJECT_OWNER && Number(env.GITHUB_PROJECT_NUMBER) && dueno === env.GITHUB_PROJECT_OWNER.toLowerCase());
}

export const CAMPO_ESTADO_QA = "Estado QA";

/**
 * Agrega el issue al tablero de la organización y, si el tablero tiene el campo "Estado QA" y la
 * opción pedida, la completa. Devuelve un aviso si el campo no existe (se crea a mano una vez).
 */
export async function agregarAlTablero(env: Env, nodeId: string, estadoQa?: string): Promise<string | null> {
    const { organization } = await graphql<{
        organization: { projectV2: { id: string; field: { id: string; options?: { id: string; name: string }[] } | null } | null } | null;
    }>(
        env,
        `query($org: String!, $numero: Int!, $campo: String!) {
            organization(login: $org) { projectV2(number: $numero) { id field(name: $campo) { ... on ProjectV2SingleSelectField { id options { id name } } } } }
        }`,
        { org: env.GITHUB_PROJECT_OWNER, numero: Number(env.GITHUB_PROJECT_NUMBER), campo: CAMPO_ESTADO_QA },
    );
    const tablero = organization?.projectV2;
    if (!tablero) throw new GithubError(`No se encontró el tablero ${env.GITHUB_PROJECT_NUMBER} de ${env.GITHUB_PROJECT_OWNER}.`);
    const { addProjectV2ItemById } = await graphql<{ addProjectV2ItemById: { item: { id: string } } }>(
        env,
        `mutation($proyecto: ID!, $contenido: ID!) { addProjectV2ItemById(input: { projectId: $proyecto, contentId: $contenido }) { item { id } } }`,
        { proyecto: tablero.id, contenido: nodeId },
    );
    if (!estadoQa) return null;
    const opcion = tablero.field?.options?.find((o) => o.name.toLowerCase() === estadoQa.toLowerCase());
    if (!tablero.field || !opcion) return `El tablero no tiene el campo "${CAMPO_ESTADO_QA}" con la opción "${estadoQa}": se agregó el issue sin completarlo.`;
    await graphql(
        env,
        `mutation($proyecto: ID!, $item: ID!, $campo: ID!, $opcion: String!) {
            updateProjectV2ItemFieldValue(input: { projectId: $proyecto, itemId: $item, fieldId: $campo, value: { singleSelectOptionId: $opcion } }) { projectV2Item { id } }
        }`,
        { proyecto: tablero.id, item: addProjectV2ItemById.item.id, campo: tablero.field.id, opcion: opcion.id },
    );
    return null;
}
