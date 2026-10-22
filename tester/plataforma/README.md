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
| `GET /health` | No | `{ ok, version, d1, r2, ultima_cron }`. 200 si responden D1 y R2; 503 si no. Lo consulta la pestaña **Estado** de `/admin` (`QA_PLATFORM_URL`). |
| `/api/*` | Sí (#126) | API de la plataforma (#127 en adelante). Hoy cualquier ruta devuelve 404. |

## Desarrollo local

Requisitos: Node 22 o superior y **npm 11** (npm 10 falla al resolver `@cloudflare/vitest-plugin`). Si tu Node trae npm 10, usá `npx -y npm@11.6.2 <comando>` en lugar de `npm`.

```bash
cd tester/plataforma
npm install
npm run migrate:local   # crea el D1 simulado en .wrangler/
npm run dev             # build de la web + Worker en http://127.0.0.1:8788
```

- `npm run dev:web` levanta Vite con recarga en caliente (puerto 5173) y manda `/api` y `/health` al Worker de `npm run dev`.
- Para probar el Cron en local: `curl "http://127.0.0.1:8788/cdn-cgi/handler/scheduled?cron=*/10+*+*+*+*"`.
- En local, D1 y R2 son simulados (Miniflare): no hace falta cuenta de Cloudflare.

### Tests

```bash
npm run typecheck   # Worker y web
npm test            # build de la web + Vitest dentro del runtime de Workers
```

Los tests corren con `@cloudflare/vitest-plugin` sobre D1 y R2 simulados, con las migraciones aplicadas.

## Secretos

Ninguno todavía. Los agregan sus issues y se cargan con `wrangler secret put <NOMBRE>` (en local, en `.dev.vars`, que no se versiona):

| Secreto | Issue | Para qué |
| :--- | :--- | :--- |
| `CLERK_SECRET_KEY` | #126 | Login con la instancia de Clerk de `/admin` |
| `GITHUB_TOKEN` | #130 | Publicar issues y sincronizar su estado |

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
