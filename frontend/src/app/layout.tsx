import "./globals.css";
import type { Metadata } from "next";

import { ChatWidget } from "@/components/ChatWidget";
import { Header } from "@/components/Header";
import { DotGrid } from "@/components/shaders/DotGrid";
import { Toaster } from "@/components/Toaster";

export const metadata: Metadata = {
  title: "PricePulse — цены маркетплейсов в одном поиске",
  description: "Сравнение цен Wildberries, Ozon, Яндекс Маркета и магазинов Рунета. Локальный AI-ассистент.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        {/* Site-wide interactive dot grid that reacts to the cursor.
            Sits at z-0; everything visible has explicit positioning above. */}
        <DotGrid />

        <div className="relative z-10">
          <Header />
          <main className="max-w-[1240px] mx-auto px-6 pt-8 pb-16">{children}</main>
        </div>

        <ChatWidget />
        <Toaster />
      </body>
    </html>
  );
}
