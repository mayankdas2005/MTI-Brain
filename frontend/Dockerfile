# ── Stage 1: Dependencies ──────────────────────────────────────
FROM node:20-alpine AS deps

RUN apk add --no-cache libc6-compat

WORKDIR /app

COPY package.json package-lock.json* ./

RUN npm ci --ignore-scripts && \
    npm cache clean --force

# ── Stage 2: Build ─────────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# NEXT_PUBLIC_* values come from frontend/.env and frontend/.env.production,
# which Next.js auto-loads during `npm run build` with NODE_ENV=production.
# Don't set NEXT_PUBLIC_* here - Dockerfile ENV beats the .env files in
# Next.js's precedence and silently overrides the intended prod values.
ENV NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production

RUN npm run build

# ── Stage 3: Production Runner ─────────────────────────────────
FROM node:20-alpine AS runner

LABEL org.opencontainers.image.title="MTI Brain Frontend" \
      org.opencontainers.image.description="MTI Brain - AI-powered decision intelligence"

WORKDIR /app

# Remove unnecessary packages and shells for security
RUN apk --no-cache add wget && \
    rm -rf /var/cache/apk/*

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0 \
    NODE_OPTIONS="--max-old-space-size=256"

# Security: non-root user
RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 --ingroup nodejs nextjs

# Pre-create .next cache dir with correct ownership
RUN mkdir -p /app/.next/cache && chown -R nextjs:nodejs /app/.next

# Copy only what's needed - minimal attack surface
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
  CMD ["wget", "-q", "-t", "1", "--spider", "http://127.0.0.1:3000/"]

CMD ["node", "server.js"]
