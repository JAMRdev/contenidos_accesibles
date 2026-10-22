// Bindings del Worker (ver wrangler.toml).
export interface Env {
    ASSETS: Fetcher;
    CF_VERSION: WorkerVersionMetadata;
    DB: D1Database;
    EVIDENCIAS: R2Bucket;
    LIMITE_API: RateLimit;
    // Clerk: la misma instancia que el panel /admin del backend
    CLERK_PUBLISHABLE_KEY: string;
    CLERK_JWT_KEY: string;
    CLERK_SECRET_KEY: string;
    // Cuentas de GitHub que son admin siempre (separadas por comas)
    QA_ADMINS: string;
    // Orígenes extra que pueden pedir tokens (por ejemplo Vite en local), separados por comas
    ORIGENES_EXTRA?: string;
}
