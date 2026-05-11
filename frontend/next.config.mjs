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
const nextConfig = {
  output: "standalone",
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
  // Defense-in-depth security headers. CSP is permissive (matches the
  // permissive local-dev posture) - but X-Content-Type-Options, Referrer-Policy,
  // and X-Frame-Options survive the Okta migration and cost nothing.
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
