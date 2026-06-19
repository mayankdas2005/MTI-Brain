import os from "node:os";

// Resolve `allowedDevOrigins` at server startup so we don't hardcode any
// particular VM's IP. Next.js 16+ rejects HMR websocket upgrades from
// non-allowed origins, which causes the page to reload in a loop when
// `next dev` is reached from a different host (VM ↔ laptop).
//
// Strategy:
//   1. Auto-detect every non-internal IPv4 on the host - covers both the
//      "dev runs on the VM, opened from a laptop" and "dev runs on a
//      laptop, opened from the LAN" cases without any config.
//   2. Merge in any comma-separated origins from `NEXT_DEV_ORIGINS` -
//      escape hatch for Docker dev, SSH tunnels, or DNS aliases that
//      auto-detect can't see.
//
// This only affects `next dev`; `next start` (production) ignores it.
const autoDetected = Object.values(os.networkInterfaces() ?? {})
  .flat()
  .filter((ni) => ni && ni.family === "IPv4" && !ni.internal)
  .map((ni) => ni.address);

const fromEnv = (process.env.NEXT_DEV_ORIGINS || "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

const allowedDevOrigins = Array.from(new Set([...autoDetected, ...fromEnv]));

/** @type {import('next').NextConfig} */
const BUILD_ID = `build-${Date.now()}`;
const BUILD_DATE = new Date();
const APP_VERSION = `${BUILD_DATE.getFullYear()}.${String(BUILD_DATE.getMonth() + 1).padStart(2, '0')}.${String(BUILD_DATE.getDate()).padStart(2, '0')}`;

const nextConfig = {
  output: "standalone",
  basePath: process.env.NEXT_PUBLIC_BASE_PATH || "",
  // Generate a unique build ID per deployment so the client can detect new versions.
  generateBuildId: () => BUILD_ID,
  // Expose build ID and app version to client code.
  env: { NEXT_PUBLIC_BUILD_ID: BUILD_ID, NEXT_PUBLIC_APP_VERSION: APP_VERSION },
  typescript: {
    // Was true. Flipped to false after a clean tsc --noEmit pass; keep
    // it strict so future regressions fail the build instead of shipping.
    ignoreBuildErrors: false,
  },
  // Auto-memoize most hooks/components without manual useMemo/useCallback.
  // React 19.2 + React Compiler is stable; the visible chat-streaming work is
  // exactly the kind of high-frequency render path that benefits most.
  reactCompiler: true,
  images: {
    unoptimized: true,
  },
  allowedDevOrigins,
  // Defense-in-depth security headers.
  // CSP uses unsafe-inline + unsafe-eval because Next.js hydration scripts and
  // the React Compiler require them. frame-ancestors, base-uri, and object-src
  // provide meaningful protection even under that constraint.
  // connect-src is intentionally open (*) because the API URL is runtime-
  // configurable via NEXT_PUBLIC_API_URL and PostHog host varies by deployment.
  async headers() {
    const csp = [
      "default-src 'self'",
      "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
      "style-src 'self' 'unsafe-inline' https://fonts.cdnfonts.com https://fonts.googleapis.com",
      "img-src 'self' data: blob:",
      "font-src 'self' data: https://fonts.cdnfonts.com https://fonts.gstatic.com",
      "connect-src *",
      "worker-src 'self' blob:",
      "frame-src 'none'",
      "frame-ancestors 'none'",
      "base-uri 'self'",
      "object-src 'none'",
      "form-action 'self'",
    ].join("; ");

    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
          { key: "Content-Security-Policy", value: csp },
        ],
      },
    ];
  },
};

export default nextConfig;
