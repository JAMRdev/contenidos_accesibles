# Catálogo de casos de prueba

Los casos de la [guía de QA](../GUIA_QA_BACKEND.md) con un ID estable, un archivo por sección. Es la **fuente de verdad**: la plataforma de QA los lista, registra sus resultados y marca con su ID los bugs que publica en GitHub ([#125](https://github.com/e18-innovalab/contenidos_accesibles/issues/125)).

## Un caso

```yaml
# yaml-language-server: $schema=./esquema.json
seccion: "4.8"                       # número de la sección de la guía
titulo: "POST /adaptations"
casos:
  - id: QA-4.8-03                    # QA-<sección>-NN: único y para siempre
    titulo: Un fragment con datos personales responde 422
    endpoint: POST /api/v1/adaptations          # opcional (los flujos no tienen uno solo)
    precondiciones: [Un diagnóstico con una barrera global.]   # opcional
    pasos:
      - Pedir una propuesta con un fragment con datos personales ficticios, con ?mock=true.
    datos: Datos personales ficticios (sección 3.1).          # opcional, nunca datos reales
    esperado: 422 sensitive_data_detected.
    severidad: alta                  # alta, media o baja (sección 9 de la guía)
    etiquetas: [adaptaciones, datos-personales]               # opcional
    mock: true                       # se prueba con ?mock=true (por defecto false)
    ia_real: false                   # consume IA real: coordinar con Backend (por defecto false)
    destructivo: false               # solo en local o staging, nunca en producción (por defecto false)
```

Con la extensión YAML de VS Code, la primera línea activa el autocompletado y la validación con [`esquema.json`](esquema.json).

> Si `esperado` es solo un número (`404.`), va entre comillas (`"404."`): si no, YAML lo lee como número.

## Reglas

- **Un ID no se reutiliza nunca.** Un bug viejo puede referenciarlo. Si se borra un caso, su ID va a [`ids-retirados.txt`](ids-retirados.txt) y el caso nuevo toma el siguiente número libre.
- **La guía muestra los IDs** (columna ID de las tablas). El texto del caso se mantiene acá; si lo cambiás, revisá que la fila de la guía siga diciendo lo mismo.
- `ia_real: true` y `destructivo: true` sirven para filtrar en la plataforma lo que no se puede probar en producción.

## Validación

Desde `tester/plataforma`:

```bash
npm run catalogo
```

Falla si un archivo no cumple el esquema, si hay IDs repetidos o de otra sección, si se usa un ID retirado o si la guía y el catálogo no tienen los mismos IDs. En la CI, además, compara contra la rama base: borrar un caso sin retirar su ID hace fallar el PR. El mismo comando regenera `esquema.json` (se versiona) y el catálogo que se empaqueta en el Worker.
