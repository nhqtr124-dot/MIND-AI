import type { NextConfig } from "next";

const api = process.env.MIND_API_URL ?? "http://localhost:8000";

const config: NextConfig = {
  reactStrictMode: true,
  transpilePackages: ["@mind/api-client", "@mind/shared-types", "@mind/ui"],
  // Same-origin proxy: httpOnly auth cookies and CSRF work without cross-site CORS.
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: `${api}/api/v1/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(self), geolocation=()" },
        ],
      },
    ];
  },
};

export default config;
