// Tests del validador del catálogo (node --test). Arman un catálogo chico en una carpeta temporal.
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { validarCatalogo, type Opciones } from "./catalogo-lib.ts";

const CASO = (id: string, extra = "") => `
  - id: ${id}
    titulo: Caso ${id}
    pasos: [Llamar al endpoint.]
    esperado: "200."
    severidad: media
${extra}`;

function catalogo(archivos: Record<string, string>, opciones: Partial<Opciones> = {}) {
    const dir = mkdtempSync(join(tmpdir(), "catalogo-"));
    for (const [nombre, texto] of Object.entries(archivos)) writeFileSync(join(dir, nombre), texto);
    return validarCatalogo({ casos: dir, guia: "QA-4.3-01 QA-4.3-02", retirados: "", idsAnteriores: null, ...opciones });
}

const VALIDO = `seccion: "4.3"\ntitulo: Process text\ncasos:${CASO("QA-4.3-01")}${CASO("QA-4.3-02", "    mock: true")}`;

test("un catálogo válido se empaqueta con la sección y los valores por defecto", () => {
    const { errores, catalogo: c } = catalogo({ "04-03.yml": VALIDO });
    assert.deepEqual(errores, []);
    assert.equal(c.casos.length, 2);
    assert.deepEqual(c.secciones, [{ seccion: "4.3", titulo: "Process text", archivo: "04-03.yml", casos: 2 }]);
    assert.deepEqual(c.casos[0], {
        id: "QA-4.3-01", titulo: "Caso QA-4.3-01", pasos: ["Llamar al endpoint."], esperado: "200.", severidad: "media",
        precondiciones: [], etiquetas: [], mock: false, ia_real: false, destructivo: false, seccion: "4.3",
    });
    assert.equal(c.casos[1].mock, true);
});

test("un campo inválido se informa con el ID del caso", () => {
    const yml = `seccion: "4.3"\ntitulo: X\ncasos:${CASO("QA-4.3-01")}\n  - id: QA-4.3-02\n    titulo: Y\n    pasos: [a]\n    esperado: 404\n    severidad: urgente\n`;
    const { errores } = catalogo({ "a.yml": yml });
    assert.ok(errores.some((e) => e.includes("QA-4.3-02.esperado")));
    assert.ok(errores.some((e) => e.includes("QA-4.3-02.severidad")));
});

test("un campo desconocido, mock e IA real a la vez o un YAML con claves repetidas fallan", () => {
    const desconocido = `seccion: "4.3"\ntitulo: X\ncasos:${CASO("QA-4.3-01", "    prioridad: alta")}`;
    const ambos = `seccion: "4.3"\ntitulo: X\ncasos:${CASO("QA-4.3-02", "    mock: true\n    ia_real: true")}`;
    const repetida = `seccion: "4.3"\nseccion: "4.4"\ntitulo: X\ncasos: []\n`;
    assert.ok(catalogo({ "a.yml": desconocido }).errores.some((e) => e.includes("QA-4.3-01")));
    assert.ok(catalogo({ "a.yml": ambos }).errores.some((e) => e.includes("mock e IA real")));
    assert.match(catalogo({ "a.yml": repetida }).errores[0], /a\.yml: .*unique/i);
});

test("IDs repetidos o de otra sección fallan", () => {
    const otro = `seccion: "4.4"\ntitulo: X\ncasos:${CASO("QA-4.3-01")}`;
    const { errores } = catalogo({ "a.yml": VALIDO, "b.yml": otro });
    assert.ok(errores.some((e) => e.includes("QA-4.3-01 repetido")));
    assert.ok(errores.some((e) => e.includes("no corresponde a la sección 4.4")));
});

test("un ID retirado no se puede reutilizar", () => {
    const { errores } = catalogo({ "a.yml": VALIDO }, { retirados: "# viejo\nQA-4.3-02  # se fusionó con 01\n" });
    assert.deepEqual(errores, ["QA-4.3-02 está retirado (ids-retirados.txt): usá un ID nuevo."]);
});

test("borrar un caso exige retirar su ID", () => {
    const anteriores = ["QA-4.3-01", "QA-4.3-02", "QA-4.3-03"];
    assert.deepEqual(catalogo({ "a.yml": VALIDO }, { idsAnteriores: anteriores }).errores, [
        "QA-4.3-03 se borró del catálogo: agregalo a tester/casos/ids-retirados.txt para que no se reutilice.",
    ]);
    assert.deepEqual(catalogo({ "a.yml": VALIDO }, { idsAnteriores: anteriores, retirados: "QA-4.3-03" }).errores, []);
});

test("la guía y el catálogo tienen que tener los mismos IDs", () => {
    const { errores } = catalogo({ "a.yml": VALIDO }, { guia: "Ver QA-4.3-01 y QA-4.9-01." });
    assert.deepEqual(errores.sort(), ["La guía menciona QA-4.9-01, que no está en el catálogo.", "QA-4.3-02 no aparece en la guía."]);
});
