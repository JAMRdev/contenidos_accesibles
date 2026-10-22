// Tests del Worker dentro del runtime de Workers (Miniflare): D1 y R2 simulados, sin cuenta de Cloudflare.
import { cloudflareTest, readD1Migrations } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

export default defineConfig(async () => {
    const migrations = await readD1Migrations("./migrations");
    return {
        plugins: [
            cloudflareTest({
                wrangler: { configPath: "./wrangler.toml" },
                miniflare: { bindings: { TEST_MIGRATIONS: migrations } },
            }),
        ],
        test: {
            include: ["test/**/*.test.ts"],
            setupFiles: ["./test/apply-migrations.ts"],
        },
    };
});
