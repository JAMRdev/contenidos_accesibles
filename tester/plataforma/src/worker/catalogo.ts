// Catálogo de casos empaquetado en el build (npm run catalogo genera catalogo.gen.json desde
// tester/casos/*.yml). Cambia solo con un deploy.
import type { Catalogo } from "../catalogo/esquema.ts";
import datos from "./catalogo.gen.json";

export const catalogo = datos as Catalogo;
