import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  // Backend is on :8000 in dev — proxy under /api so cookies stay same-origin.
  async rewrites() {
    const backend = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
    return [
      { source: "/api/backend/:path*", destination: `${backend}/:path*` },
    ];
  },
  images: {
    // marketplace CDNs we expect to display
    remotePatterns: [
      { protocol: "https", hostname: "**.wbbasket.ru" },
      { protocol: "https", hostname: "**.wb.ru" },
      { protocol: "https", hostname: "**.ozone.ru" },
      { protocol: "https", hostname: "**.ozon.ru" },
      { protocol: "https", hostname: "**.yandex.net" },
      { protocol: "https", hostname: "**.yandex.ru" },
      { protocol: "https", hostname: "**.mvideo.ru" },
      { protocol: "https", hostname: "**.megamarket.ru" },
    ],
  },
};

export default config;
