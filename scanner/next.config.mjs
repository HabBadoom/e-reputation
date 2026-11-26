/** @type {import('next').NextConfig} */
// AynPulse Scanner web app. Talks only to the Scanner API (/api/v1/scanner/*) through a
// same-origin rewrite, so its httpOnly `sc_*` cookies never need CORS.
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
  async rewrites() {
    // Only the Scanner part of the API is reachable from this origin.
    return [{ source: "/api/v1/scanner/:path*", destination: `${backend}/api/v1/scanner/:path*` }];
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};
