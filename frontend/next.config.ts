import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,

  // Next 16 blocks dev resources (HMR websocket, RSC chunks) from any
  // origin not in this list. Without it the client bundle never
  // hydrates → white screen.
  allowedDevOrigins: [
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "10.8.0.8",       // local VPN/LAN
  ],

  async rewrites() {
    const backend = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
    return [
      { source: "/api/backend/:path*", destination: `${backend}/:path*` },
    ];
  },

  images: {
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
