// Bindings del Worker (ver wrangler.toml).
export interface Env {
    ASSETS: Fetcher;
    CF_VERSION: WorkerVersionMetadata;
    DB: D1Database;
    EVIDENCIAS: R2Bucket;
}
