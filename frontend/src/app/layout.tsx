import "./globals.css";

import type { Metadata } from "next";

import { ChatWidget } from "@/components/ChatWidget";
import { Footer } from "@/components/Footer";
import { Header } from "@/components/Header";

export const metadata: Metadata = {
  title: "PricePulse — поиск цен на маркетплейсах",
  description:
    "Сравните цены товаров на Wildberries, Ozon, Яндекс Маркете и в открытом Рунете за один запрос.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body>
        <Header />
        <main className="max-w-[1240px] mx-auto px-6 py-8">{children}</main>
        <Footer />
        <ChatWidget />
      </body>
    </html>
  );
}
