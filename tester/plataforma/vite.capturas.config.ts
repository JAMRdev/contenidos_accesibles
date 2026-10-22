// Entorno de capturas para revisar el diseño sin login ni Worker: Clerk simulado y la API con datos
// de ejemplo (src/web/dev). Solo para desarrollo:
//   npm run capturas          →   http://127.0.0.1:5174/dev/capturas.html?ruta=/bugs/1
//   npm run capturas:build    →   versión estática en dist/capturas (para capturas automáticas)
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
    root: "src/web",
    base: "./",
    plugins: [react()],
    resolve: {
        alias: { "@clerk/react": fileURLToPath(new URL("./src/web/dev/clerk-falso.tsx", import.meta.url)) },
    },
    server: { host: "127.0.0.1", port: 5174, strictPort: true },
    build: {
        outDir: "../../dist/capturas",
        emptyOutDir: true,
        rollupOptions: { input: fileURLToPath(new URL("./src/web/dev/capturas.html", import.meta.url)) },
    },
});
