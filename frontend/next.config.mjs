/** @type {import('next').NextConfig} */
const backend = process.env.REPINTEL_BACKEND_URL ?? "http://localhost:8000";

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

export default {
  reactStrictMode: true,
  poweredByHeader: false,
  output: "standalone",
  // Same-origin proxy: the browser only ever talks to the Next.js origin, so auth cookies
  // can be httpOnly + SameSite=Lax and no CORS is needed.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};
