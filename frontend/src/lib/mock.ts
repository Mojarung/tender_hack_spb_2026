import type { ProductOffer, RankedOffer } from "./types";

const now = new Date().toISOString();

export const MOCK_OFFERS: ProductOffer[] = [
  { source: "wb", name: "Apple iPhone 15 128 GB, Чёрный", price: "53196", currency: "RUB",
    url: "https://www.wildberries.ru/catalog/825188791/detail.aspx",
    image: "https://basket-21.wbbasket.ru/vol8251/part825188/825188791/images/big/1.webp",
    characteristics: { brand: "Apple", rating: "4.9", feedbacks: "1234" },
    seller: "PSC", rating: 4.9, fetched_at: now, cached: false },
  { source: "ozon", name: "Apple iPhone 15 128GB Black", price: "54990", currency: "RUB",
    url: "https://www.ozon.ru/product/iphone-15-128gb/",
    image: "https://ir.ozone.ru/s3/multimedia-z/c1000/6815517099.jpg",
    characteristics: { rating: "4.8", reviews: "873" },
    seller: "RE-Store", rating: 4.8, fetched_at: now, cached: false },
  { source: "ya_market", name: "Apple iPhone 15 128 ГБ, чёрный", price: "55490", currency: "RUB",
    url: "https://market.yandex.ru/product--apple-iphone-15-128gb/",
    image: null,
    characteristics: { brand: "Apple", rating: "4.7" },
    seller: "Яндекс Маркет", rating: 4.7, fetched_at: now, cached: false },
  { source: "runet", name: "iPhone 15 128GB (Ростест)", price: "52900", currency: "RUB",
    url: "https://re-store.ru/iphone-15-128-black/", image: null,
    characteristics: { site: "re-store.ru" },
    seller: "Re:Store", rating: null, fetched_at: now, cached: false },
  { source: "wb", name: "MacBook Air M3 13″ 256 GB", price: "94560", currency: "RUB",
    url: "https://www.wildberries.ru/catalog/824000000/detail.aspx",
    image: "https://basket-20.wbbasket.ru/vol8240/part824000/824000000/images/big/1.webp",
    characteristics: { brand: "Apple", rating: "4.95", feedbacks: "412" },
    seller: "Apple Russia", rating: 4.95, fetched_at: now, cached: false },
  { source: "ozon", name: "Робот-пылесос Xiaomi Vacuum X20+", price: "29990", currency: "RUB",
    url: "https://www.ozon.ru/product/xiaomi-vacuum-x20/", image: null,
    characteristics: { rating: "4.6", reviews: "1502" },
    seller: "Xiaomi", rating: 4.6, fetched_at: now, cached: false },
  { source: "ya_market", name: "Sony WH-1000XM5 беспроводные наушники", price: "32500",
    currency: "RUB", url: "https://market.yandex.ru/product--sony-wh-1000xm5/", image: null,
    characteristics: { brand: "Sony", rating: "4.85" },
    seller: "Sony Store", rating: 4.85, fetched_at: now, cached: false },
  { source: "runet", name: "Sony WH-1000XM5 (новый)", price: "31290", currency: "RUB",
    url: "https://citilink.ru/sony-wh-1000xm5/", image: null,
    characteristics: { site: "citilink.ru" },
    seller: "Citilink", rating: null, fetched_at: now, cached: false },
];

export const MOCK_TOP_DEALS: RankedOffer[] = MOCK_OFFERS.slice(0, 4).map((offer, i) => ({
  offer, score: Number((1 - i * 0.18).toFixed(3)), rank: i + 1,
}));
