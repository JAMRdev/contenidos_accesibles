// `env` de cloudflare:test: los bindings del Worker más lo que agrega vitest.config.ts.
import type { D1Migration } from "@cloudflare/vitest-plugin";
import type { Env as WorkerEnv } from "../src/worker/env";

declare global {
    namespace Cloudflare {
        interface Env extends WorkerEnv {
            TEST_MIGRATIONS: D1Migration[];
            TEST_CLERK_PRIVATE_KEY: string;
        }
    }
}
