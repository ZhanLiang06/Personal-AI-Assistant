# Deployment

This project uses a local-first deployment model:

- Frontend: Cloudflare Pages, built from `web/` with Vite
- Backend: local FastAPI server, JSON only
- Public frontend domain: `https://bojiakpui-xyz-student-web-app.me`
- Public API domain: `https://api.bojiakpui-xyz-student-web-app.me`
- Backend exposure: Cloudflare named tunnel to `http://localhost:8000`
- Browser authentication: Cloudflare Access
- Streaming: Server-Sent Events (SSE)

## Request Flow

```text
Browser
-> Cloudflare Pages frontend (static build of web/)
-> api.bojiakpui-xyz-student-web-app.me
-> Cloudflare Access
-> Cloudflare Tunnel
-> local FastAPI
-> local agent
```

## Cloudflare Pages

The frontend is now a Vite build, not a folder of hand-written files, so Pages
needs a build command. Settings:

| Setting | Value |
| --- | --- |
| Framework preset | None (or Vite) |
| Build command | `npm ci && npm run build` |
| Build output directory | `web/dist` |
| Root directory | `web` |
| Node version | 20 or newer |

With **Root directory** set to `web`, the build command runs inside that folder
and the output directory is `dist` relative to it. If your Pages project has no
root-directory setting, leave it blank and use:

```text
Build command:  npm ci --prefix web && npm run build --prefix web
Output:         web/dist
```

### Environment variable

Set this for both Production and Preview:

```text
VITE_API_BASE_URL = https://api.bojiakpui-xyz-student-web-app.me
```

It is baked in at build time, so changing it requires a redeploy. The app also
falls back to that same URL if the variable is missing, and uses relative paths
when served from localhost.

### SPA routing

`/finance` is a client-side route. `web/public/_redirects` ships this rule so a
hard refresh on it does not 404:

```text
/*    /index.html   200
```

## Cloudflare Tunnel

Unchanged. The named tunnel routes only the API subdomain to FastAPI:

```text
api.bojiakpui-xyz-student-web-app.me -> http://localhost:8000
```

The root frontend domain stays on Pages:

```text
bojiakpui-xyz-student-web-app.me -> Cloudflare Pages
```

## Cloudflare Access And CORS

Unchanged. The API is behind Access, and the browser sends credentialed JSON
cross-origin, so it preflights:

```text
OPTIONS /chat/stream
```

Access must allow preflight through:

```text
Bypass OPTIONS requests to origin: ON
```

FastAPI then answers with the CORS headers from `ALLOWED_ORIGINS` in
`src/api/main.py`. That list now includes the Vite dev ports (5173, 4173) and
no longer includes `:8000`, because FastAPI no longer serves any HTML.

## Local Development

Two processes. The API:

```powershell
uv run uvicorn src.api.main:app --reload --host 127.0.0.1 --port 8000
```

The frontend:

```powershell
npm install --prefix web
npm run dev --prefix web
```

Then open `http://localhost:5173`. Vite proxies `/api`, `/chat`,
`/conversations`, and `/health` to FastAPI, so there is no CORS in development.
`/finance` is deliberately not proxied — it is a page route, while the finance
JSON lives under `/api/finance`.

To check the production bundle locally:

```powershell
npm run build --prefix web
npm run preview --prefix web
```

## Verification Checklist

Frontend:

```powershell
curl.exe -L -I https://bojiakpui-xyz-student-web-app.me/
```

Expected: `Content-Type: text/html`.

SPA route:

```powershell
curl.exe -L -I https://bojiakpui-xyz-student-web-app.me/finance
```

Expected: `200` and `Content-Type: text/html` — not a 404. A 404 here means
`_redirects` did not ship.

CORS preflight:

```powershell
curl.exe -i -X OPTIONS https://api.bojiakpui-xyz-student-web-app.me/chat/stream `
  -H "Origin: https://bojiakpui-xyz-student-web-app.me" `
  -H "Access-Control-Request-Method: POST" `
  -H "Access-Control-Request-Headers: content-type"
```

Expected:

```text
Access-Control-Allow-Origin: https://bojiakpui-xyz-student-web-app.me
Access-Control-Allow-Credentials: true
```

Finally, open the frontend and send a message. If Access asks for login,
authenticate against the API domain first:

```text
https://api.bojiakpui-xyz-student-web-app.me/health
```
