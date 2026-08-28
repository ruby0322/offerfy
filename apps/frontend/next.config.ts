import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

const withNextIntl = createNextIntlPlugin("./i18n/request.ts");
const projectRoot = dirname(fileURLToPath(import.meta.url));

/** Dest uvicorn / dest Next. Must never be baked into a public `next build`. */
const DEST_BACKEND = /(?:127\.0\.0\.1|localhost):(3001|3100|8002)\b/i;
const PUBLIC_BACKEND = "http://127.0.0.1:8001";

function backendRewriteBase(): string {
  const raw = (process.env.BACKEND_INTERNAL_URL || "").trim().replace(/\/$/, "");
  if (process.env.NODE_ENV === "production") {
    const url = raw || PUBLIC_BACKEND;
    if (DEST_BACKEND.test(url)) {
      throw new Error(
        `BACKEND_INTERNAL_URL=${url} is dest. Public next build bakes /api rewrites into .next; use BACKEND_INTERNAL_URL=${PUBLIC_BACKEND} (see docs/deploy.md).`,
      );
    }
    return url;
  }
  return raw || "http://backend:8000";
}

const nextConfig: NextConfig = {
  turbopack: {
    root: projectRoot,
  },
  async rewrites() {
    const backendBase = backendRewriteBase();
    return [
      {
        source: "/api/:path*",
        destination: `${backendBase}/:path*`,
      },
    ];
  },
};

export default withNextIntl(nextConfig);
