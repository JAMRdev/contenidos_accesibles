// Valida el catálogo de casos (tester/casos/*.yml) y lo empaqueta para el Worker (Issue #125).
//
//   npm run catalogo
//
// Falla (código 1) si un archivo no cumple el esquema, si hay IDs repetidos, si se reutiliza un ID
// retirado, si un ID del catálogo no aparece en la guía (o al revés) o, con CATALOGO_BASE=<ref de git>,
// si se borró un caso sin agregar su ID a casos/ids-retirados.txt.
//
// Escribe src/worker/catalogo.gen.json (no se versiona) y casos/esquema.json (para el autocompletado
// de los .yml en el editor; se versiona).
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";
import { z } from "zod";
import { archivoSchema } from "../src/catalogo/esquema.ts";
import { idsDelArchivo, leerYaml, validarCatalogo } from "./catalogo-lib.ts";

const PLATAFORMA = join(import.meta.dirname, "..");
const REPO = join(PLATAFORMA, "..", "..");
const CASOS = join(REPO, "tester", "casos");
const RETIRADOS = join(CASOS, "ids-retirados.txt");

// IDs del catálogo en otra versión del repo (por ejemplo, la rama base del PR)
function idsEnGit(ref: string, errores: string[]): string[] | null {
    const git = (...args: string[]) => execFileSync("git", args, { cwd: REPO, encoding: "utf8" });
    let archivos: string[];
    try {
        archivos = git("ls-tree", "--name-only", `${ref}:tester/casos`).split("\n").filter((f) => f.endsWith(".yml"));
    } catch {
        return null; // la base todavía no tiene catálogo
    }
    return archivos.flatMap((f) => idsDelArchivo(leerYaml(git("show", `${ref}:tester/casos/${f}`), `${ref}:${f}`, errores)));
}

const erroresGit: string[] = [];
const base = process.env.CATALOGO_BASE;
const { errores, catalogo } = validarCatalogo({
    casos: CASOS,
    guia: readFileSync(join(REPO, "tester", "GUIA_QA_BACKEND.md"), "utf8"),
    retirados: existsSync(RETIRADOS) ? readFileSync(RETIRADOS, "utf8") : "",
    idsAnteriores: base ? idsEnGit(base, erroresGit) : null,
    nombre: (ruta) => relative(REPO, ruta),
});
errores.push(...erroresGit);

if (errores.length) {
    console.error(`El catálogo tiene ${errores.length} error(es):\n${errores.map((e) => `  - ${e}`).join("\n")}`);
    process.exit(1);
}

writeFileSync(join(PLATAFORMA, "src", "worker", "catalogo.gen.json"), `${JSON.stringify(catalogo, null, 2)}\n`);
writeFileSync(join(CASOS, "esquema.json"), `${JSON.stringify(z.toJSONSchema(archivoSchema, { io: "input" }), null, 2)}\n`);
console.log(`Catálogo OK: ${catalogo.casos.length} casos en ${catalogo.secciones.length} secciones.`);
