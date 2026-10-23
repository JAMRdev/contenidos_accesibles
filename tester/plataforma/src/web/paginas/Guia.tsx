// Guía de uso para QA: muestra tester/plataforma/GUIA.md (el mismo archivo que se lee en GitHub).
// Renderiza solo el Markdown que usa la guía: títulos, párrafos, listas, citas e imágenes.
import type { ReactNode } from "react";
import guia from "../../../GUIA.md?raw";

// En el repo las imágenes se referencian desde la carpeta de la plataforma; en la web se sirven desde /guia/
const imagen = (src: string) => src.replace(/^src\/web\/public\//, "/");

/** Negrita, código y enlaces dentro de una línea. */
function enLinea(texto: string): ReactNode[] {
    const partes: ReactNode[] = [];
    const patron = /\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\)/g;
    let desde = 0;
    for (const m of texto.matchAll(patron)) {
        if (m.index > desde) partes.push(texto.slice(desde, m.index));
        const k = partes.length;
        if (m[1]) partes.push(<strong key={k}>{enLinea(m[1])}</strong>);
        else if (m[2]) partes.push(<code key={k}>{m[2]}</code>);
        else
            partes.push(
                <a key={k} href={m[4]} target="_blank" rel="noopener">
                    {m[3]}
                </a>,
            );
        desde = m.index + m[0].length;
    }
    if (desde < texto.length) partes.push(texto.slice(desde));
    return partes;
}

type Bloque =
    | { tipo: "titulo"; nivel: number; texto: string }
    | { tipo: "parrafo" | "cita"; texto: string }
    | { tipo: "lista"; ordenada: boolean; items: string[] }
    | { tipo: "imagen"; alt: string; src: string };

function bloques(md: string): Bloque[] {
    const salida: Bloque[] = [];
    for (const trozo of md.trim().split(/\n\s*\n/)) {
        const lineas = trozo.split("\n");
        const titulo = /^(#{1,3}) (.+)$/.exec(trozo);
        const img = /^!\[([^\]]*)\]\(([^)]+)\)$/.exec(trozo);
        if (titulo) salida.push({ tipo: "titulo", nivel: titulo[1].length, texto: titulo[2] });
        else if (img) salida.push({ tipo: "imagen", alt: img[1], src: imagen(img[2]) });
        else if (lineas.every((l) => l.startsWith("> "))) salida.push({ tipo: "cita", texto: lineas.map((l) => l.slice(2)).join(" ") });
        else if (lineas.every((l) => /^(- |\d+\. )/.test(l)))
            salida.push({ tipo: "lista", ordenada: /^\d/.test(lineas[0]), items: lineas.map((l) => l.replace(/^(- |\d+\. )/, "")) });
        else salida.push({ tipo: "parrafo", texto: lineas.join(" ") });
    }
    return salida;
}

function Bloque({ b }: { b: Bloque }) {
    switch (b.tipo) {
        case "titulo":
            if (b.nivel === 1) return <h1 className="admin-h1">{b.texto}</h1>;
            return b.nivel === 2 ? <h2 className="admin-h2">{enLinea(b.texto)}</h2> : <h3 className="qa-guia-h3">{enLinea(b.texto)}</h3>;
        case "parrafo":
            return <p>{enLinea(b.texto)}</p>;
        case "cita":
            return <p className="admin-note">{enLinea(b.texto)}</p>;
        case "imagen":
            return (
                <figure className="qa-guia-figura">
                    <img src={b.src} alt={b.alt} loading="lazy" />
                    <figcaption>{b.alt}</figcaption>
                </figure>
            );
        case "lista": {
            const items = b.items.map((it, i) => <li key={i}>{enLinea(it)}</li>);
            return b.ordenada ? <ol>{items}</ol> : <ul>{items}</ul>;
        }
    }
}

export function Guia() {
    return (
        <article className="qa-guia">
            {bloques(guia).map((b, i) => (
                <Bloque key={i} b={b} />
            ))}
        </article>
    );
}
