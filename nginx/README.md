# MTI Brain — nginx

Reverse proxy for MTI Brain. Handles TLS termination, routes traffic between the Next.js frontend and FastAPI backend, enforces rate limits, and adds security response headers.

## Role in the Stack

nginx sits in front of all user-facing traffic. It is launched by the root `docker-compose.yml` and communicates with the `frontend` and `backend` containers over the internal `app_net` Docker network.

```
Browser
  │ HTTPS :443 / HTTP :80
  ▼
nginx (nginx:1.27-alpine)
  ├─ /api/v1/auth/login  ──────────────────→ backend:8000  (strict rate limit)
  ├─ /api/v1/chat/*/ask|retry|edit  ───────→ backend:8000  (600 s SSE timeout)
  ├─ /api/  ───────────────────────────────→ backend:8000  (30 s timeout)
  └─ /  ───────────────────────────────────→ frontend:3000
```

## Key Configuration

### TLS

Certificates are mounted read-only at `/etc/nginx/ssl/`:

| File | Description |
|------|-------------|
| `fullchain.pem` | Certificate chain (certificate + intermediates) |
| `privkey.pem` | Private key |

Protocol: TLSv1.2 and TLSv1.3 only. ECDHE-only AEAD cipher suite (Mozilla Intermediate profile). Session tickets are disabled until a shared rotating ticket key is deployed.

HTTP (port 80) redirects to HTTPS (port 443) with a 301.

### Rate Limiting

Two shared zones (10 MB each, ~160k binary IPs per zone):

| Zone | Rate | Burst | Applied to |
|------|------|-------|-----------|
| `login` | 5 req/min | 3 (nodelay) | `POST /api/v1/auth/login` |
| `api` | 120 req/min | 10–20 (nodelay) | All `/api/*` endpoints |

HTTP 429 is returned when a limit is exceeded. The `api` zone rate (120 req/min) is set at 4× the backend's `ask_per_minute = 30` to account for burst without front-running the backend's own slowapi limiter.

### Proxy Timeouts

| Location | `proxy_read_timeout` | Reason |
|----------|---------------------|--------|
| `POST .../auth/login` | 15 s | Short — credential check only |
| `POST .../chat/*/ask\|retry\|edit` | **600 s** | SSE streams; Gunicorn timeout is 480 s, nginx gives a safety margin |
| All other `/api/*` | 30 s | Non-streaming endpoints |
| `/` (frontend) | 60 s | Next.js responses |

Buffering (`proxy_buffering off`) and caching (`proxy_cache off`) are disabled on all backend locations so SSE events reach the client immediately.

### Security Headers

Added to all HTTPS responses:

| Header | Value |
|--------|-------|
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |

`Server` and `X-Powered-By` headers from upstream responses are stripped (`proxy_hide_header`). nginx version disclosure is suppressed (`server_tokens off`).

### Static Asset Caching

| Location | Cache-Control |
|----------|--------------|
| `/_next/static/*` | Immutable (set by Next.js — `max-age=31536000, immutable`) |
| `*.png\|jpg\|jpeg\|gif\|ico\|svg\|webp\|woff2\|woff\|ttf\|eot` | `public, max-age=86400` (1 day) |

### Other Settings

- **Client max body size:** 5 MB (sufficient for JSON API payloads; no file-upload endpoints)
- **Gzip compression:** enabled for `text/plain`, `text/css`, `application/json`, `application/javascript`, and XML/RSS types
- **WebSocket support:** `Upgrade` / `Connection` headers are set on the frontend proxy location for Next.js HMR in development

## Container

| Property | Value |
|----------|-------|
| Image | `nginx:1.27-alpine` |
| Ports | `80:80`, `443:443` |
| Memory limit | 64 MB |
| CPU limit | 0.25 |
| Filesystem | Read-only root FS; tmpfs for `/var/run`, `/var/cache/nginx`, `/tmp` |
| Logs | stdout/stderr → Docker logging driver |

Certificates volume must be mounted at `/etc/nginx/ssl/` before the container starts. In production this is a bind mount from the EC2 host where certs are managed by Certbot or placed manually.

## Files

```
nginx/
├── nginx.conf   # Full nginx configuration
└── README.md    # This file
```

---

## Related Documentation

| Component | README |
|-----------|--------|
| Root (architecture + quick start) | [../README.md](../README.md) |
| Backend (FastAPI) | [../backend/README.md](../backend/README.md) |
| Frontend (Next.js) | [../frontend/README.md](../frontend/README.md) |
| Deployment (AWS CodeDeploy) | [../deploy/README.md](../deploy/README.md) |
