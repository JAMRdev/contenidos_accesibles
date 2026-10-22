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
```

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

Las variables públicas (`CLERK_PUBLISHABLE_KEY`, `CLERK_JWT_KEY`, `QA_ADMINS`) están en `[vars]` de `wrangler.toml`.

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
