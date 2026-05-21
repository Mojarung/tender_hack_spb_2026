import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Search, Loader2, ArrowRight, Heart, ExternalLink, Sliders, CheckCircle2, AlertTriangle, HelpCircle } from 'lucide-react';
import { executeQueryStream, ScrapingTelemetryStep } from '../lib/api';
import { ProductOffer, SourceKind, SearchResponse, SourceGroup, RankedOffer } from '../lib/types';
import { MOCK_SENTIMENT, MOCK_QUERIES } from '../lib/mock';

interface SearchDashboardProps {
  isDemoMode: boolean;
  onOpenProduct: (offer: ProductOffer) => void;
  onAddFavorite: (offer: ProductOffer) => void;
  favorites: ProductOffer[];
}

export const SearchDashboard: React.FC<SearchDashboardProps> = ({
  isDemoMode,
  onOpenProduct,
  onAddFavorite,
  favorites
}) => {
  const [query, setQuery] = useState('iphone 15 128gb');
  const [searchQuery, setSearchQuery] = useState('');
  const [offers, setOffers] = useState<ProductOffer[]>([]);
  const [telemetry, setTelemetry] = useState<ScrapingTelemetryStep[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [tookMs, setTookMs] = useState<number | null>(null);
  
  // Formula Weights State
  const [priceWeight, setPriceWeight] = useState(60);
  const [ratingWeight, setRatingWeight] = useState(20);
  const [sentimentWeight, setSentimentWeight] = useState(20);
  
  // Active Source Filters
  const [sourceFilter, setSourceFilter] = useState<'all' | SourceKind>('all');

  // Trigger search on mount and query updates
  useEffect(() => {
    handleSearch(query);
  }, [query]);

  const handleSearch = (qString: string) => {
    if (!qString.trim()) return;
    setIsLoading(true);
    setOffers([]);
    setTelemetry([]);
    setSourceFilter('all');
    setTookMs(null);

    const stream = executeQueryStream(qString, isDemoMode, (event, payload) => {
      switch (event) {
        case 'query':
          // Can display normalised query if needed
          break;
        case 'telemetry':
          setTelemetry(payload as ScrapingTelemetryStep[]);
          break;
        case 'offer':
          setOffers(prev => [...prev, payload as ProductOffer]);
          break;
        case 'group':
          // Group completed, individual offers are already added
          break;
        case 'done':
          setTookMs(payload.took_ms);
          setIsLoading(false);
          break;
        case 'error':
          setIsLoading(false);
          break;
      }
    });

    return () => stream.cancel();
  };

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (searchQuery.trim()) {
      setQuery(searchQuery);
    }
  };

  // ────────────── MATH MODEL: DYNAMIC BEST-DEAL SCORE ──────────────
  const prices = offers.map(o => o.price);
  const maxPrice = Math.max(...prices) || 1;
  const minPrice = Math.min(...prices) || 0;
  const priceRange = maxPrice - minPrice || 1;

  const calculateDynamicScore = (offer: ProductOffer): number => {
    // 1. Price Score: lower is better (0 to 100 points)
    const priceScore = 100 * (1 - (offer.price - minPrice) / priceRange);

    // 2. Rating Score (0 to 100 points)
    const ratingScore = (offer.rating || 4.5) * 20;

    // 3. Sentiment Score: Fetch positive rating percentage (0 to 100 points)
    const sentimentKey = offer.name.toLowerCase().includes('macbook') ? 'macbook-air-m3' : 'iphone-15';
    const sentimentInfo = MOCK_SENTIMENT[sentimentKey] || { positive: 75 };
    const sentimentScore = sentimentInfo.positive;

    // 4. Combined Weighted Score
    const totalWeight = priceWeight + ratingWeight + sentimentWeight || 1;
    const finalScore = (priceScore * priceWeight + ratingScore * ratingWeight + sentimentScore * sentimentWeight) / totalWeight;

    return Math.round(finalScore * 10) / 10;
  };

  // Dynamic ranking and sorting
  const rankedOffers: RankedOffer[] = offers
    .map(offer => ({
      offer,
      score: calculateDynamicScore(offer),
      rank: 0 // Will set after sorting
    }))
    .sort((a, b) => b.score - a.score)
    .map((item, idx) => ({
      ...item,
      rank: idx + 1
    }));

  const filteredRanked = sourceFilter === 'all' 
    ? rankedOffers 
    : rankedOffers.filter(item => item.offer.source === sourceFilter);

  const bestDeal = rankedOffers[0];
  const otherDeals = sourceFilter === 'all' 
    ? rankedOffers.slice(1) 
    : filteredRanked;

  const getSourceIconColor = (source: SourceKind) => {
    switch (source) {
      case SourceKind.WB: return '#cb11ab';
      case SourceKind.OZON: return '#0061f2';
      case SourceKind.YA_MARKET: return '#ffc73a';
      default: return '#10b981';
    }
  };

  const getSourceLabel = (source: SourceKind) => {
    switch (source) {
      case SourceKind.WB: return 'WB';
      case SourceKind.OZON: return 'Ozon';
      case SourceKind.YA_MARKET: return 'Маркет';
      default: return 'Рунет';
    }
  };

  const getTelemetryStatusIcon = (status: string) => {
    switch (status) {
      case 'done': return <CheckCircle2 size={14} color="var(--color-emerald)" />;
      case 'failed': return <AlertTriangle size={14} color="var(--color-rose)" />;
      case 'connecting':
      case 'scraping':
      case 'analyzing':
        return <Loader2 size={14} color="var(--color-accent)" className="animate-spin" />;
      default:
        return <HelpCircle size={14} color="var(--color-text-dim)" />;
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      {/* Top Search Hero */}
      <div className="search-hero">
        <h1>Интеллектуальный поиск лучших цен</h1>
        <p>Агрегатор товаров в режиме реального времени с анализом отзывов и расчетом рейтинга сделки</p>
        
        <form onSubmit={onSubmit} className="search-bar-container">
          <Search size={20} color="var(--color-text-muted)" style={{ marginRight: '12px' }} />
          <input 
            type="text" 
            className="search-input" 
            placeholder="Что вы ищете? Например: iPhone 15, Dyson V15..." 
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
          />
          <button type="submit" className="btn-premium" style={{ borderRadius: '12px', padding: '8px 20px' }}>
            Найти
          </button>
        </form>

        <div className="pills-container">
          {MOCK_QUERIES.map(item => (
            <button 
              key={item.raw} 
              className={`pill-item ${query === item.raw ? 'active' : ''}`}
              onClick={() => setQuery(item.raw)}
            >
              {item.title}
            </button>
          ))}
        </div>
      </div>

      {/* Main Dashboard Section */}
      <div className="dashboard-grid">
        {/* Left Telemetry Sidebar Panel */}
        <aside className="sidebar-panel">
          {/* Scraping Progress Status */}
          <div className="glass-card" style={{ padding: '20px' }}>
            <h3 className="panel-title">Скрейпинг-каналы</h3>
            {telemetry.length === 0 ? (
              <div style={{ fontSize: '12px', color: 'var(--color-text-dim)', textAlign: 'center', padding: '10px 0' }}>
                Ввод поискового запроса...
              </div>
            ) : (
              telemetry.map(step => (
                <div key={step.source} className="source-row" style={{ borderLeft: `3px solid ${getSourceIconColor(step.source)}` }}>
                  <div className="source-left">
                    <span className="source-indicator" style={{ backgroundColor: getSourceIconColor(step.source) }} />
                    <span style={{ color: 'var(--color-text)' }}>{getSourceLabel(step.source)}</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    {step.count > 0 && <span className="source-count">{step.count} шт</span>}
                    {getTelemetryStatusIcon(step.status)}
                  </div>
                </div>
              ))
            )}
          </div>

          {/* Scoring Weights sliders panel */}
          <div className="glass-card" style={{ padding: '20px' }}>
            <h3 className="panel-title" style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <Sliders size={16} /> Формула Best-Deal
            </h3>
            
            <div className="weight-control">
              <div className="weight-label">
                <span>Вес низкой цены</span>
                <span>{priceWeight}%</span>
              </div>
              <input 
                type="range" 
                className="weight-slider" 
                value={priceWeight} 
                onChange={e => setPriceWeight(Number(e.target.value))} 
                min="0" max="100" 
              />
            </div>

            <div className="weight-control">
              <div className="weight-label">
                <span>Вес рейтинга</span>
                <span>{ratingWeight}%</span>
              </div>
              <input 
                type="range" 
                className="weight-slider" 
                value={ratingWeight} 
                onChange={e => setRatingWeight(Number(e.target.value))} 
                min="0" max="100" 
              />
            </div>

            <div className="weight-control">
              <div className="weight-label">
                <span>Вес отзывов (Sentiment)</span>
                <span>{sentimentWeight}%</span>
              </div>
              <input 
                type="range" 
                className="weight-slider" 
                value={sentimentWeight} 
                onChange={e => setSentimentWeight(Number(e.target.value))} 
                min="0" max="100" 
              />
            </div>

            <div style={{ borderTop: '1px solid var(--border-glass)', marginTop: '16px', paddingTop: '12px', fontSize: '11px', color: 'var(--color-text-dim)', lineHeight: 1.4 }}>
              Слайдеры весов мгновенно пересчитывают релевантность и переранжируют товары прямо в браузере.
            </div>
          </div>
        </aside>

        {/* Right Main Offers View */}
        <main style={{ minWidth: 0 }}>
          {/* Toolbar base Filters */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px', flexWrap: 'wrap', gap: '12px' }}>
            <div>
              <h2 style={{ fontSize: '20px', fontWeight: 800 }}>Результаты выдачи</h2>
              {tookMs && (
                <span style={{ fontSize: '12px', color: 'var(--color-text-dim)' }}>
                  Ответ сформирован за {tookMs} мс
                </span>
              )}
            </div>

            {/* Filter buttons */}
            <div style={{ display: 'flex', gap: '6px' }}>
              <button 
                className={`btn-glass ${sourceFilter === 'all' ? 'active' : ''}`}
                onClick={() => setSourceFilter('all')}
                style={{ fontSize: '11px', padding: '6px 12px' }}
              >
                Все ({offers.length})
              </button>
              {telemetry.map(step => (
                <button
                  key={step.source}
                  className={`btn-glass ${sourceFilter === step.source ? 'active' : ''}`}
                  onClick={() => setSourceFilter(step.source)}
                  style={{ fontSize: '11px', padding: '6px 12px' }}
                  disabled={step.count === 0}
                >
                  {getSourceLabel(step.source)} ({step.count})
                </button>
              ))}
            </div>
          </div>

          {/* Product list renderer */}
          {offers.length === 0 ? (
            <div className="glass-card" style={{ padding: '60px', textAlign: 'center', color: 'var(--color-text-muted)' }}>
              {isLoading ? (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '16px' }}>
                  <Loader2 size={36} color="var(--color-accent)" className="animate-spin" />
                  <div>Скрейпинг маркетплейсов и денормализация запроса...</div>
                </div>
              ) : (
                <div>Здесь появятся найденные предложения</div>
              )}
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
              {/* Highlight Best Deal #1 Card (only in global 'all' search mode) */}
              {bestDeal && sourceFilter === 'all' && (
                <motion.div 
                  layoutId={bestDeal.offer.name + bestDeal.offer.source}
                  className="glass-card best-deal-glow best-deal-hero-card"
                  style={{ borderRadius: 'var(--radius-lg)', overflow: 'hidden', padding: '24px' }}
                >
                  <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'space-between', gap: '16px' }}>
                    <div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                        <span style={{ fontSize: '18px' }}>👑</span>
                        <span style={{ background: 'var(--color-emerald)', color: 'white', fontSize: '10px', fontWeight: 800, padding: '2px 8px', borderRadius: '4px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                          Лучшая сделка
                        </span>
                        <span style={{ color: 'var(--color-text-dim)', fontSize: '12px' }}>
                          Ранг 1
                        </span>
                      </div>

                      <h3 
                        onClick={() => onOpenProduct(bestDeal.offer)}
                        style={{ fontSize: '20px', fontWeight: 800, color: 'white', cursor: 'pointer', marginBottom: '8px' }}
                      >
                        {bestDeal.offer.name}
                      </h3>

                      <div style={{ display: 'flex', gap: '12px', fontSize: '12px', color: 'var(--color-text-muted)', marginBottom: '12px' }}>
                        <span>Продавец: <strong>{bestDeal.offer.seller || 'Неизвестен'}</strong></span>
                        {bestDeal.offer.rating && <span>⭐ <strong>{bestDeal.offer.rating}</strong></span>}
                      </div>

                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', marginBottom: '12px' }}>
                        {Object.entries(bestDeal.offer.characteristics).slice(0, 3).map(([k, v]) => (
                          <span key={k} style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border-glass)', padding: '2px 8px', borderRadius: '4px', fontSize: '10px' }}>
                            {k}: {v}
                          </span>
                        ))}
                      </div>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderTop: '1px solid var(--border-glass)', paddingTop: '16px' }}>
                      <div style={{ display: 'flex', alignItems: 'baseline', gap: '4px' }}>
                        <span style={{ fontSize: '28px', fontWeight: 800 }}>{bestDeal.offer.price.toLocaleString()}</span>
                        <span style={{ fontSize: '16px', fontWeight: 600 }}>₽</span>
                      </div>

                      <div style={{ display: 'flex', gap: '10px' }}>
                        <button className="btn-glass" onClick={() => onAddFavorite(bestDeal.offer)}>
                          <Heart size={16} fill={favorites.some(f => f.url === bestDeal.offer.url) ? 'var(--color-rose)' : 'none'} color={favorites.some(f => f.url === bestDeal.offer.url) ? 'var(--color-rose)' : 'white'} />
                        </button>
                        <button className="btn-premium" onClick={() => onOpenProduct(bestDeal.offer)}>
                          Аналитика <ArrowRight size={16} />
                        </button>
                      </div>
                    </div>
                  </div>

                  {/* Best Deal circular score card */}
                  <div style={{ width: '160px', flexShrink: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', borderLeft: '1px solid var(--border-glass)', paddingLeft: '24px' }}>
                    <div style={{ width: '90px', height: '90px', borderRadius: '50%', background: 'linear-gradient(135deg, var(--color-accent), #a855f7)', display: 'grid', placeItems: 'center', boxShadow: '0 0 20px rgba(99,102,241,0.5)' }}>
                      <div style={{ textAlign: 'center', color: 'white' }}>
                        <div style={{ fontSize: '22px', fontWeight: 800 }}>{bestDeal.score}</div>
                        <div style={{ fontSize: '8px', fontWeight: 700, opacity: 0.8, textTransform: 'uppercase' }}>score</div>
                      </div>
                    </div>
                    <span style={{ fontSize: '11px', color: 'var(--color-text-muted)', fontWeight: 600, marginTop: '12px', textAlign: 'center' }}>
                      Рейтинг релевантности
                    </span>
                  </div>
                </motion.div>
              )}

              {/* Grid with other deals */}
              <motion.div className="product-grid" layout>
                <AnimatePresence>
                  {otherDeals.map(item => (
                    <motion.div
                      key={item.offer.url}
                      layoutId={item.offer.name + item.offer.source}
                      initial={{ opacity: 0, scale: 0.95 }}
                      animate={{ opacity: 1, scale: 1 }}
                      exit={{ opacity: 0, scale: 0.95 }}
                      className="glass-card glass-card-hover"
                      style={{ padding: '16px', display: 'flex', flexDirection: 'column', justifyContent: 'space-between', minHeight: '340px' }}
                    >
                      <div>
                        {/* Card header */}
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
                          <span style={{ fontSize: '11px', fontWeight: 700, color: 'var(--color-text-dim)', textTransform: 'uppercase' }}>
                            {getSourceLabel(item.offer.source)}
                          </span>
                          <span style={{ background: 'var(--color-accent-glow)', color: 'var(--color-accent)', padding: '2px 8px', borderRadius: '4px', fontSize: '10px', fontWeight: 700 }}>
                            Score {item.score}
                          </span>
                        </div>

                        {/* Title */}
                        <h4 
                          onClick={() => onOpenProduct(item.offer)}
                          style={{ fontSize: '14px', fontWeight: 700, color: 'white', lineHeight: 1.3, cursor: 'pointer', marginBottom: '8px', height: '36px', overflow: 'hidden', textOverflow: 'ellipsis', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}
                        >
                          {item.offer.name}
                        </h4>

                        {/* Image placeholder wrapper */}
                        <div 
                          onClick={() => onOpenProduct(item.offer)}
                          style={{ width: '100%', height: '120px', borderRadius: 'var(--radius-sm)', overflow: 'hidden', background: '#1f2937', marginBottom: '10px', cursor: 'pointer' }}
                        >
                          <img 
                            src={item.offer.image || 'https://images.unsplash.com/photo-1510557880182-3d4d3cba35a5?w=500&q=80'} 
                            alt={item.offer.name}
                            style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                          />
                        </div>

                        {/* Seller */}
                        <div style={{ fontSize: '11px', color: 'var(--color-text-muted)', marginBottom: '8px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          ⭐ {item.offer.rating || 4.5} · {item.offer.seller || 'Магазин'}
                        </div>
                      </div>

                      {/* Card footer */}
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid var(--border-glass)', paddingTop: '12px', marginTop: '10px' }}>
                        <div style={{ fontSize: '18px', fontWeight: 800 }}>
                          {item.offer.price.toLocaleString()} ₽
                        </div>

                        <div style={{ display: 'flex', gap: '6px' }}>
                          <button 
                            onClick={() => onAddFavorite(item.offer)}
                            style={{
                              width: '32px',
                              height: '32px',
                              borderRadius: '6px',
                              background: 'var(--surface-glass)',
                              border: '1px solid var(--border-glass)',
                              cursor: 'pointer',
                              display: 'grid',
                              placeItems: 'center',
                              color: favorites.some(f => f.url === item.offer.url) ? 'var(--color-rose)' : 'white'
                            }}
                          >
                            <Heart size={14} fill={favorites.some(f => f.url === item.offer.url) ? 'var(--color-rose)' : 'none'} />
                          </button>
                          
                          <button 
                            className="btn-premium" 
                            onClick={() => onOpenProduct(item.offer)}
                            style={{ padding: '6px 12px', fontSize: '11px', borderRadius: '6px' }}
                          >
                            Анализ
                          </button>
                        </div>
                      </div>
                    </motion.div>
                  ))}
                </AnimatePresence>
              </motion.div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
};
