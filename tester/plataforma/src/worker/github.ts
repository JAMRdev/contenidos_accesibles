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

export type IssueEncontrado = { numero: number; titulo: string; url: string; estado: string };

/** Issues abiertos del repo que mencionan el caso en el título o en el cuerpo (incluye la marca oculta). */
export async function buscarIssuesDelCaso(env: Env, casoId: string): Promise<IssueEncontrado[]> {
    const q = encodeURIComponent(`repo:${env.GITHUB_REPO} is:issue is:open "${casoId}" in:title,body`);
    const r = await github<{ items: { number: number; title: string; html_url: string; state: string }[] }>(env, `/search/issues?q=${q}&per_page=10`);
    return r.items.map((i) => ({ numero: i.number, titulo: i.title, url: i.html_url, estado: i.state }));
}
