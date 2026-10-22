# Plataforma de QA

Web y API para que el equipo de QA registre las rondas de prueba del backend, junte evidencias, siga el estado de los bugs y los publique en GitHub ([#123](https://github.com/e18-innovalab/contenidos_accesibles/issues/123)).

- **Producción:** <https://innovalab-qa.jamrdev2.workers.dev> (`/health` sin login)
- **Cuenta de Cloudflare:** la del proyecto (`6e7b0cff…`, ja.monsalve.otros), con recursos propios de QA.

## Arquitectura

Todo corre en Cloudflare, separado del backend de Railway y de su D1/R2: si Railway se cae o se despliega, la plataforma sigue funcionando.

| Pieza | Dónde | Notas |
| :--- | :--- | :--- |
| Web de QA | Assets del Worker (`dist/web`) | React + Vite (`src/web`). Una ruta desconocida devuelve `index.html`. |
| API | Worker `innovalab-qa` (`src/worker`) | Hono. Atiende `/api/*` y `/health`. |
| Datos | D1 `innovalab-qa` | Migraciones en `migrations/`. No es el D1 de producción. |
| Evidencias | R2 `innovalab-qa-evidencias` | Privado y **sin borrado automático**. |
| Sincronización con GitHub | Cron Trigger cada 10 min | Por ahora solo registra la corrida (`ultima_cron` en `/health`); la sincronización llega con #134. |

**Por qué un solo Worker con assets y no Pages:** la web y la API quedan en el mismo origen (sin CORS), se despliegan juntas con un solo `wrangler deploy` y Cloudflare recomienda Workers con assets para proyectos nuevos.

### Endpoints

| Ruta | Login | Qué hace |
| :--- | :--- | :--- |
| `GET /health` | No | `{ ok, version, d1, r2, ultima_cron, casos }`. 200 si responden D1 y R2; 503 si no. Lo consulta la pestaña **Estado** de `/admin` (`QA_PLATFORM_URL`). |
| `GET /api/config` | No | Publishable key de Clerk para la web. |
| `GET /api/me` | Cualquier rol | Cuenta de GitHub, rol y permisos (`escribir`, `administrar`). |
| `GET/POST /api/miembros`, `PATCH/DELETE /api/miembros/:github` | Admin | Miembros y su rol (`{ github, rol }`, `{ rol?, habilitado? }`). |
| `GET /api/auditoria?limite=&antes=` | Admin | Registro de auditoría, lo más reciente primero. |
| `GET /api/casos?area=&seccion=&etiqueta=&q=&destructivo=&mock=&ia_real=&archivados=` | Cualquier rol | Casos del repo y manuales, filtrados. |
| `GET /api/casos/secciones?area=` | Cualquier rol | Secciones (repo) o casos de uso (manuales) del área. |
| `POST /api/casos`, `PATCH /api/casos/:id` | QA | Crear o editar un caso manual (`{ area, seccion, titulo, ubicacion?, precondiciones[], pasos[], datos?, esperado, severidad, etiquetas[], mock, ia_real, destructivo }`). |
| `POST /api/casos/:id/archivar`, `…/reactivar` | QA | Los casos manuales no se borran: se archivan. |
| `GET /api/casos/:id` | Cualquier rol | Un caso, su historial en todas las sesiones (regresiones) y sus bugs. |
| `GET /api/sesiones?estado=&entorno=&autor=&q=&antes=&limite=` | Cualquier rol | Sesiones con su avance (casos por resultado), paginadas. |
| `POST /api/sesiones` | QA | `{ titulo, area?, entorno, url, version?, notas? }`. Áreas: `backend` (por defecto) y `frontend`; entornos: `produccion`, `staging`, `local`. |
| `GET /api/sesiones/:id?seccion=&resultado=&q=&destructivo=&mock=&ia_real=` | Cualquier rol | La sesión, su avance y cada caso con su resultado. |
| `PATCH /api/sesiones/:id`, `POST /api/sesiones/:id/cerrar` | QA | Editar o cerrar (solo sesiones abiertas). `POST …/reabrir`: admin. |
| `PUT/DELETE /api/sesiones/:id/resultados/:caso` | QA | `{ estado, notas? }` con `paso`, `fallo`, `bloqueado` u `omitido`; `DELETE` lo vuelve a pendiente. |
| `GET/POST /api/resultados/:id/comentarios` | POST: QA | Comentarios de un resultado (`{ texto, en_github? }`). |
| `GET /api/bugs?estado=&caso=&seccion=&entorno=&severidad=&autor=&sesion=&desde=&hasta=&q=&antes=&limite=` | Cualquier rol | Bugs filtrados y paginados. |
| `POST /api/bugs`, `PATCH /api/bugs/:id` | QA | Crear un borrador (`{ caso_id, sesion_id?, titulo, descripcion?, severidad? }`) o cambiar estado, título, descripción o severidad. |
| `GET /api/bugs/:id`, `GET/POST /api/bugs/:id/comentarios` | POST: QA | El bug con su reporte, su historial de estados y sus comentarios. |
| `GET /api/bugs/:id/vista-previa` | Cualquier rol | El issue tal como se publicaría (título, cuerpo en Markdown, etiquetas, repo) y qué le falta al reporte. |
| `GET /api/bugs/:id/duplicados` | Cualquier rol | Bugs activos del mismo caso y, con `GITHUB_TOKEN`, issues abiertos del caso en `GITHUB_REPO`. |

| `POST /api/bugs/:id/publicar` | QA | Publica el bug en GitHub (o comenta en el issue abierto del caso). Límite: 10 por minuto por usuario. |
| `GET /api/sesiones/:id/publicacion`, `POST /api/sesiones/:id/publicar` | POST: QA | Qué se va a publicar de la sesión y publicarla. |

**Publicación en GitHub (#130).** Nada se publica sin que QA lo pida y nada se cierra solo.

- **Un bug:** si hay un issue abierto del mismo caso (de otro bug de la plataforma o con la marca `<!-- qa-case: … -->` en GitHub), se comenta en él: el bug queda vinculado a ese issue o, si ya es de otro bug, descartado como duplicado. Si no, se crea el issue sin asignar, con las etiquetas de la plantilla, y se agrega al tablero con "Estado QA" = Falló si el campo existe. También se envían los comentarios marcados "también en GitHub".
- **Una sesión:** publica sus bugs en borrador (omite los incompletos), propone cerrar con un comentario los bugs abiertos de casos que ahora pasaron, envía los comentarios pendientes y crea o actualiza el issue de ejecución `[QA - Issue] Ejecución <fecha> · <entorno> · <título>` con la tabla de casos, sus bugs y sus evidencias.
- **Idempotente:** cada publicación queda en `qa_publications` con una clave única (`bug:7:issue`, `comentario:12`, `sesion:3:ejecucion`, `sesion:3:propuesta:9`). Publicar de nuevo no duplica; si GitHub falla a mitad de camino, la API responde 502, lo hecho queda registrado y el reintento sigue desde ahí (un issue creado sin guardar se recupera por la marca `<!-- qa-bug: … -->`).
- **Tablero:** solo se usa si `GITHUB_REPO` es de la misma organización que `GITHUB_PROJECT_OWNER`. En local, con un repo personal en `.dev.vars`, el tablero de la org no se toca. El campo "Estado QA" (Pasó / Falló / Bloqueado / Omitido) se crea a mano una vez en el tablero; si no existe, se avisa y el issue se agrega igual.

**Reporte de un bug (#129).** Campos: título, severidad, URL y versión probadas, pasos, resultado esperado, resultado obtenido y notas. Al crear un bug desde una sesión se precargan los pasos y el esperado del catálogo, la URL y la versión de la sesión y el obtenido de las notas del resultado. El issue sale de una sola plantilla (`src/worker/reporte.ts`): título `[QA - Issue] <caso> · <título>`, marca oculta `<!-- qa-case: <caso> -->`, etiquetas `[QA - Issue]`, `bug`, `testing` y `backend`, y enlaces a las evidencias del bug y del resultado. Para publicar no puede faltar título, pasos, esperado ni obtenido.

| `GET /api/evidencias?resultado=\|bug=` | Cualquier rol | Evidencias de un resultado o un bug. |
| `POST /api/evidencias?resultado=\|bug=` | QA | Sube un archivo: el body es el archivo, con `Content-Type`, `X-Nombre` (URL-encoded) y `X-Etiqueta` opcional. |
| `POST /api/evidencias/texto` | QA | `{ resultado_id \| bug_id, etiqueta, texto }`: request, response, curl o consola pegados. |
| `GET /api/evidencias/:id`, `GET /api/evidencias/:id/archivo` | Cualquier rol | Metadatos y archivo. |
| `DELETE /api/evidencias/:id`, `GET /api/evidencias/uso` | Admin | Borrar una evidencia (queda en la auditoría) y ver cuánto ocupa el bucket. |

### Evidencias

- **Qué se sube:** imágenes y GIF (PNG, JPEG, WebP, GIF, hasta 10 MB), video (MP4, WebM, hasta 50 MB) y texto (JSON, HAR, log, txt, hasta 2 MB). Se valida la firma del archivo, no solo la extensión. Las imágenes y los videos van directo al bucket, sin cargarse en memoria.
- **Datos sensibles:** el texto (pegado o subido) se guarda con los tokens, headers `Authorization`/`Cookie`, claves (`sk_`, `pk_`, `AIza…`, `gsk_`, `ghp_`…), JWT y firmas de URLs ocultos (`src/worker/redactar.ts`). Las capturas y los videos no se pueden revisar solos: la web le recuerda a QA usar solo los datos de prueba de la guía.
- **La plataforma no ejecuta llamadas contra el backend.** QA prueba con Swagger, la web de pruebas o curl y carga lo que obtuvo. Así no se manejan tokens de docente y se puede probar igual el backend local.
- **Enlaces desde GitHub:** apuntan a la página `/evidencias/:id` de la web, que pide login (el repo es público y las evidencias no). La web pide el archivo con el token de sesión y lo muestra desde un blob local.
- **Sin borrado automático:** solo un admin borra evidencias. Un resultado con evidencias no vuelve a pendiente (se cambia de estado).

### Áreas y casos manuales

La plataforma sirve para el backend y para el frontend. Los casos del repo (`tester/casos`) son del área **backend** y se cambian con un PR. Los **casos manuales** se crean desde la web (Catálogo → Nuevo caso) en cualquier área, agrupados por **caso de uso** (por ejemplo "Login de docentes"), y viven en `qa_cases`:

- Su ID lo asigna la plataforma por área (`QA-FE-001`, `QA-BE-001`…) y no se reutiliza: un caso que ya no se usa se **archiva**, así sus resultados y bugs lo siguen nombrando.
- Cada **sesión** prueba un área y lista solo sus casos vigentes (más los archivados que ya tengan resultado en esa sesión).
- Cada **bug** hereda el área de su caso: el issue lleva la etiqueta `backend` o `frontend`, y el issue de ejecución, el área en el título.

**Resultados y bugs son cosas distintas.** El resultado es lo que dio un caso en una sesión. El bug es lo que se publica en GitHub y tiene su propio estado: `borrador` → (publicar, #130) → `abierto` → `en_curso` → `resuelto` → `verificado`, más `reabierto` y `descartado`. Sin issue, un bug solo pasa entre `borrador` y `descartado`; publicado, QA puede moverlo entre los estados publicados y GitHub lo actualiza con la sincronización (#134). Cada cambio queda en el historial del bug con su origen (`qa`, `github` o `sincronizacion`).

**Casos destructivos** (estrés, cambios de configuración): no se pueden registrar en una sesión de producción (409) y no cuentan en su avance.

Toda la API, menos `/api/config`, exige `Authorization: Bearer <token de sesión de Clerk>`: sin token o con uno inválido responde **401**; con una cuenta sin rol, **403**. Cada usuario tiene un límite de 120 requests por minuto (**429** con `Retry-After`).

### Login y roles

- **Login:** la web usa la **misma instancia de Clerk que `/admin`** (botón de GitHub). El Worker valida el token de sesión sin llamadas de red con la clave pública (`CLERK_JWT_KEY`), y solo si lo emitió esa instancia (`iss`) para la web de QA (`azp` = origen de la plataforma o uno de `ORIGENES_EXTRA`).
- **Quién es:** el usuario de GitHub sale de la API de Clerk (cuenta de GitHub verificada por OAuth) y se guarda una hora en `qa_users`. No pasa por Railway: si el backend está caído, el login sigue andando.
- **Roles:**

  | Rol | Puede |
  | :--- | :--- |
  | Solo lectura (`viewer`) | Ver sesiones, resultados, bugs y evidencias (por ejemplo, abrir los enlaces de los issues) |
  | QA (`qa`) | Además, crear sesiones, registrar resultados, subir evidencias, comentar, cambiar estados y publicar |
  | Admin (`admin`) | Además, administrar miembros, ver la auditoría y borrar evidencias |

- **Cómo se asigna un rol:** los usuarios de `QA_ADMINS` (`wrangler.toml`, los mismos admins del panel) son admin siempre. El resto lo agrega un admin desde la sección **Miembros** de la web (o `POST /api/miembros`). Los miembros viven en el D1 de QA, no en el backend: el acceso a `/admin` y el acceso a QA son independientes.
- **Auditoría:** los cambios de miembros y los accesos denegados quedan en `qa_audit` (quién, qué y cuándo).

### Catálogo de casos

Los casos viven en [`tester/casos/`](../casos/) y se **empaquetan en el build**: `npm run catalogo` los valida y genera `src/worker/catalogo.gen.json` (no se versiona), que el Worker importa. `build`, `dev`, `deploy`, `typecheck` y `test` lo corren antes.

Por qué empaquetado y no leído desde GitHub: cada deploy usa exactamente los casos de su commit, la plataforma no depende de GitHub ni de su rate limit para listar casos y no hay caché que invalidar. Cambiar un caso requiere un deploy, que el workflow hace solo al mergear en `main`.

## Desarrollo local

Requisitos: Node 22 o superior y **npm 11** (npm 10 falla al resolver `@cloudflare/vitest-plugin`). Si tu Node trae npm 10, usá `npx -y npm@11.6.2 <comando>` en lugar de `npm`.

```bash
cd tester/plataforma
npm install
npm run migrate:local   # crea el D1 simulado en .wrangler/
npm run dev             # build de la web + Worker en http://127.0.0.1:8788
```

Para el login, creá `.dev.vars` (no se versiona) antes de `npm run dev`:

```ini
CLERK_SECRET_KEY=sk_test_...          # ADMIN_CLERK_SECRET_KEY del .env del backend
ORIGENES_EXTRA=http://localhost:5173  # para npm run dev:web
GITHUB_TOKEN=ghp_...                  # token clásico con public_repo (o repo) y project
GITHUB_REPO=usuario/repo-de-prueba    # opcional: en local, publicar en un repo de prueba
```

Los tests no leen nada de `.dev.vars`: `vitest.config.ts` fija sus propios valores (sin token y con Clerk y GitHub simulados).

- `npm run dev:web` levanta Vite con recarga en caliente (puerto 5173) y manda `/api` y `/health` al Worker de `npm run dev`.
- Para probar el Cron en local: `curl "http://127.0.0.1:8788/cdn-cgi/handler/scheduled?cron=*/10+*+*+*+*"`.
- En local, D1 y R2 son simulados (Miniflare): no hace falta cuenta de Cloudflare.

### Tests

```bash
npm run typecheck   # catálogo, Worker, web y scripts
npm test            # tests del validador del catálogo + Vitest dentro del runtime de Workers
```

Los tests del Worker corren con `@cloudflare/vitest-plugin` sobre D1 y R2 simulados, con las migraciones aplicadas. Los del validador (`scripts/catalogo.test.ts`) corren con el runner de Node.

## Secretos

Se cargan con `wrangler secret put <NOMBRE>` (en local, en `.dev.vars`, que no se versiona). Nunca llegan al navegador.

| Secreto | Issue | Para qué |
| :--- | :--- | :--- |
| `CLERK_SECRET_KEY` | #126 | Leer en la API de Clerk la cuenta de GitHub de quien inicia sesión (`ADMIN_CLERK_SECRET_KEY` del backend) |
| `GITHUB_TOKEN` | #130 | Publicar issues y sincronizar su estado |

Las variables públicas (`CLERK_PUBLISHABLE_KEY`, `CLERK_JWT_KEY`, `QA_ADMINS`, `GITHUB_REPO`) están en `[vars]` de `wrangler.toml`. Sin `GITHUB_TOKEN` la plataforma funciona igual: solo no consulta ni publica en GitHub.

## Deploy

**Automático:** el workflow `.github/workflows/tester-plataforma.yml` corre typecheck y tests en cada PR que toca `tester/**`. En `main` aplica las migraciones y despliega, **solo si** el repo tiene el secret `CLOUDFLARE_API_TOKEN` (token de API con permisos de Workers Scripts, D1 y R2 sobre la cuenta del proyecto). Sin ese secret, el job avisa y no despliega.

**A mano** (con `wrangler login` en la cuenta del proyecto):

```bash
npm run migrate:remote
npm run deploy
```

### Recursos (se crearon una sola vez)

```bash
npm run d1:create   # copiar el database_id a wrangler.toml
npm run r2:create
```

El bucket no tiene reglas de ciclo de vida: no agregar ninguna, porque los issues enlazan evidencias que tienen que seguir disponibles.
