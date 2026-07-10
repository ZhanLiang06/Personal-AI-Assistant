# Deployment

This project uses a local-first deployment model:

- Frontend: Cloudflare Pages
- Backend: local FastAPI server
- Public frontend domain: `https://bojiakpui-xyz-student-web-app.me`
- Public API domain: `https://api.bojiakpui-xyz-student-web-app.me`
- Backend exposure: Cloudflare named tunnel to `http://localhost:8000`
- Browser authentication: Cloudflare Access
- Streaming: Server-Sent Events (SSE)

## Request Flow

```text
Browser
-> Cloudflare Pages frontend
-> api.bojiakpui-xyz-student-web-app.me
-> Cloudflare Access
-> Cloudflare Tunnel
-> local FastAPI
-> local agent
```

## Cloudflare Pages

Pages should deploy the static frontend from:

```text
src/web
```

The deployed site should expose:

```text
/
/static/app.js
/static/styles.css
```

The frontend config in `src/web/index.html` points browser API calls at:

```text
https://api.bojiakpui-xyz-student-web-app.me
```

## Cloudflare Tunnel

The named tunnel should route only the API subdomain to the local FastAPI server:

```text
api.bojiakpui-xyz-student-web-app.me -> http://localhost:8000
```

The root frontend domain should not route to the tunnel:

```text
bojiakpui-xyz-student-web-app.me -> Cloudflare Pages
```

## Cloudflare Access And CORS

The API is protected by Cloudflare Access. Since the browser sends JSON with credentials to a different origin, the browser performs a CORS preflight:

```text
OPTIONS /chat/stream
```

For this setup, Cloudflare Access should allow preflight requests to reach FastAPI:

```text
Bypass OPTIONS requests to origin: ON
```

FastAPI then responds with the CORS headers configured in `src/api/main.py`.

## Verification Checklist

Check the frontend:

```powershell
curl.exe -L -I https://bojiakpui-xyz-student-web-app.me/
```

Expected: `Content-Type: text/html`.

Check JavaScript:

```powershell
curl.exe -L -I https://bojiakpui-xyz-student-web-app.me/static/app.js
```

Expected: `Content-Type: application/javascript` or `text/javascript`.

Check CSS:

```powershell
curl.exe -L -I https://bojiakpui-xyz-student-web-app.me/static/styles.css
```

Expected: `Content-Type: text/css`.

Check CORS preflight:

```powershell
curl.exe -i -X OPTIONS https://api.bojiakpui-xyz-student-web-app.me/chat/stream `
  -H "Origin: https://bojiakpui-xyz-student-web-app.me" `
  -H "Access-Control-Request-Method: POST" `
  -H "Access-Control-Request-Headers: content-type"
```

Expected response headers:

```text
Access-Control-Allow-Origin: https://bojiakpui-xyz-student-web-app.me
Access-Control-Allow-Credentials: true
Access-Control-Allow-Methods: GET, POST
```

Finally, open the frontend in a browser and send a message:

```text
https://bojiakpui-xyz-student-web-app.me
```

If Cloudflare Access asks for login, authenticate first against the API domain:

```text
https://api.bojiakpui-xyz-student-web-app.me/health
```
