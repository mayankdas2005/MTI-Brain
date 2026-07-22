# ── Stage 1: Build ─────────────────────────────────────────────
FROM mti-brain-frontend-base:latest AS builder

WORKDIR /app

COPY . .

ENV NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production

RUN npm run build

# ── Stage 2: Production Runner ─────────────────────────────────
FROM node:20-alpine AS runner

LABEL org.opencontainers.image.title="MTI Brain Frontend" \
      org.opencontainers.image.description="MTI Brain - AI-powered decision intelligence"

WORKDIR /app

RUN apk --no-cache add wget && \
    rm -rf /var/cache/apk/*

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0 \
    NODE_OPTIONS="--max-old-space-size=256"

RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 --ingroup nodejs nextjs

RUN mkdir -p /app/.next/cache && chown -R nextjs:nodejs /app/.next

COPY --from=builder --chown=nextjs:nodejs /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
  CMD ["wget", "-q", "-t", "1", "--spider", "http://127.0.0.1:3000/"]

CMD ["node", "server.js"]
