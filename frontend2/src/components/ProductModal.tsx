import React from 'react';
import { X, TrendingDown, ThumbsUp, Calendar, Heart, Shield } from 'lucide-react';
import { ProductOffer, SourceKind } from '../lib/types';
import { MOCK_SENTIMENT, generatePriceHistory } from '../lib/mock';

interface ProductModalProps {
  offer: ProductOffer;
  onClose: () => void;
  onAddFavorite: (offer: ProductOffer) => void;
  isFavorite: boolean;
}

export const ProductModal: React.FC<ProductModalProps> = ({
  offer,
  onClose,
  onAddFavorite,
  isFavorite
}) => {
  // Generate mock price history based on current price
  const priceHistory = generatePriceHistory(offer.price);
  
  // Get sentiment details if available, or fallback to a standard mock sentiment
  const sentimentKey = offer.name.toLowerCase().includes('macbook') ? 'macbook-air-m3' : 'iphone-15';
  const sentiment = MOCK_SENTIMENT[sentimentKey] || {
    positive: 75,
    neutral: 18,
    negative: 7,
    quotes: {
      positive: [
        { text: 'Отличный товар, полностью соответствует заявленным характеристикам. Покупкой очень доволен!', rating: 5, votes_plus: 12, created: '2026-05-19T10:00:00Z' }
      ],
      neutral: [
        { text: 'Качество хорошее, но доставка немного задержалась. В целом рекомендую к покупке.', rating: 4, votes_plus: 3, created: '2026-05-18T14:20:00Z' }
      ],
      negative: [
        { text: 'Цена завышена для такого набора функций. Ожидал большего.', rating: 3, votes_plus: 8, created: '2026-05-15T11:10:00Z' }
      ]
    }
  };

  // Sparkline coordinates helper
  const maxPrice = Math.max(...priceHistory.map(p => p.price));
  const minPrice = Math.min(...priceHistory.map(p => p.price));
  const priceRange = maxPrice - minPrice || 1;

  const pointsStr = priceHistory
    .map((p, i) => {
      const x = (i / (priceHistory.length - 1)) * 400 + 10;
      const y = 140 - ((p.price - minPrice) / priceRange) * 100 - 10;
      return `${x},${y}`;
    })
    .join(' ');

  const getSourceIcon = (source: SourceKind) => {
    switch (source) {
      case SourceKind.WB: return '🟣';
      case SourceKind.OZON: return '🔵';
      case SourceKind.YA_MARKET: return '🟡';
      default: return '🟢';
    }
  };

  const getSourceName = (source: SourceKind) => {
    switch (source) {
      case SourceKind.WB: return 'Wildberries';
      case SourceKind.OZON: return 'Ozon';
      case SourceKind.YA_MARKET: return 'Яндекс Маркет';
      default: return 'Открытый Рунет';
    }
  };

  const formatDate = (dateStr: string) => {
    const d = new Date(dateStr);
    return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' });
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div 
        className="modal-content glass-card" 
        onClick={e => e.stopPropagation()}
        style={{
          padding: '24px',
          maxWidth: '850px',
          border: '1px solid var(--border-glass-hover)',
          animation: 'modalSlideIn 0.3s cubic-bezier(0.16, 1, 0.3, 1)'
        }}
      >
        {/* Style for anims */}
        <style dangerouslySetInnerHTML={{__html: `
          @keyframes modalSlideIn {
            from { transform: translateY(20px); opacity: 0; }
            to { transform: translateY(0); opacity: 1; }
          }
        `}} />

        {/* Modal Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '24px', gap: '16px' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '8px' }}>
              <span style={{ fontSize: '18px' }}>{getSourceIcon(offer.source)}</span>
              <span style={{ fontSize: '14px', fontWeight: 700, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                {getSourceName(offer.source)}
              </span>
              {offer.cached && (
                <span style={{ background: 'var(--color-accent-glow)', color: 'var(--color-accent)', padding: '2px 8px', borderRadius: '4px', fontSize: '10px', fontWeight: 700 }}>
                  ИЗ КЭША
                </span>
              )}
            </div>
            <h2 style={{ fontSize: '24px', fontWeight: 800, color: 'var(--color-text)', lineHeight: 1.2 }}>
              {offer.name}
            </h2>
          </div>
          <button className="btn-icon" onClick={onClose} style={{ flexShrink: 0 }}>
            <X size={20} />
          </button>
        </div>

        {/* Modal Layout Grid */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.2fr', gap: '32px', alignItems: 'start' }}>
          {/* Left Column: Image, Price summary, Price History */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
            <div style={{ width: '100%', height: '240px', borderRadius: 'var(--radius-md)', overflow: 'hidden', border: '1px solid var(--border-glass)', background: '#1f2937', position: 'relative' }}>
              <img 
                src={offer.image || 'https://images.unsplash.com/photo-1510557880182-3d4d3cba35a5?w=500&q=80'} 
                alt={offer.name}
                style={{ width: '100%', height: '100%', objectFit: 'cover' }}
              />
              <button 
                onClick={() => onAddFavorite(offer)}
                style={{
                  position: 'absolute',
                  top: '12px',
                  right: '12px',
                  width: '38px',
                  height: '38px',
                  borderRadius: '50%',
                  background: 'rgba(3, 7, 18, 0.6)',
                  border: '1px solid var(--border-glass)',
                  cursor: 'pointer',
                  display: 'grid',
                  placeItems: 'center',
                  color: isFavorite ? 'var(--color-rose)' : 'white',
                  transition: 'var(--transition-fast)'
                }}
              >
                <Heart size={18} fill={isFavorite ? 'currentColor' : 'none'} />
              </button>
            </div>

            {/* Price section */}
            <div style={{ background: 'rgba(0, 0, 0, 0.2)', padding: '16px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-glass)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <span style={{ fontSize: '12px', color: 'var(--color-text-dim)', fontWeight: 600 }}>АКТУАЛЬНАЯ ЦЕНА</span>
                  <div style={{ fontSize: '28px', fontWeight: 800, color: 'var(--color-text)', display: 'flex', alignItems: 'baseline', gap: '4px' }}>
                    {offer.price.toLocaleString('ru-RU')} <span style={{ fontSize: '16px', fontWeight: 600 }}>₽</span>
                  </div>
                </div>
                
                <a 
                  href={offer.url} 
                  target="_blank" 
                  rel="noopener noreferrer" 
                  className="btn-premium"
                  style={{ textDecoration: 'none', padding: '10px 16px' }}
                >
                  Купить
                </a>
              </div>
              <div style={{ display: 'flex', gap: '12px', marginTop: '12px', paddingTop: '12px', borderTop: '1px solid var(--border-glass)', fontSize: '12px', color: 'var(--color-text-muted)' }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                  <Shield size={14} color="var(--color-emerald)" /> Продавец: <strong>{offer.seller || 'Не указан'}</strong>
                </span>
                {offer.rating && (
                  <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '4px' }}>
                    ⭐ <strong>{offer.rating}</strong>
                  </span>
                )}
              </div>
            </div>

            {/* Price History Sparkline */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                <h3 style={{ fontSize: '14px', fontWeight: 700, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                  История цен
                </h3>
                <span style={{ fontSize: '11px', color: 'var(--color-emerald)', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '4px' }}>
                  <TrendingDown size={14} /> -5.3% за 2 недели
                </span>
              </div>

              <div style={{ background: 'rgba(0,0,0,0.15)', border: '1px solid var(--border-glass)', padding: '12px', borderRadius: 'var(--radius-md)', display: 'flex', justifyContent: 'center' }}>
                <svg width="100%" height="140" viewBox="0 0 420 140" style={{ overflow: 'visible' }}>
                  {/* Grid lines */}
                  <line x1="10" y1="10" x2="410" y2="10" stroke="var(--border-glass)" strokeDasharray="4" />
                  <line x1="10" y1="65" x2="410" y2="65" stroke="var(--border-glass)" strokeDasharray="4" />
                  <line x1="10" y1="120" x2="410" y2="120" stroke="var(--border-glass)" strokeDasharray="4" />
                  
                  {/* Sparkline curve */}
                  <polyline
                    fill="none"
                    stroke="var(--color-accent)"
                    strokeWidth="3.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    points={pointsStr}
                  />

                  {/* Dot anchors */}
                  {priceHistory.map((p, i) => {
                    const x = (i / (priceHistory.length - 1)) * 400 + 10;
                    const y = 140 - ((p.price - minPrice) / priceRange) * 100 - 10;
                    return (
                      <g key={i} className="spark-dot">
                        <circle cx={x} cy={y} r="5" fill="var(--color-accent)" />
                        <circle cx={x} cy={y} r="8" fill="var(--color-accent-glow)" stroke="var(--color-accent)" strokeWidth="1" style={{ opacity: 0.5 }} />
                      </g>
                    );
                  })}
                  
                  {/* Labels */}
                  <text x="10" y="135" fill="var(--color-text-dim)" fontSize="9" fontWeight="600">{formatDate(priceHistory[0].ts)}</text>
                  <text x="360" y="135" fill="var(--color-text-dim)" fontSize="9" fontWeight="600">{formatDate(priceHistory[priceHistory.length - 1].ts)}</text>
                  
                  <text x="12" y="24" fill="var(--color-text-dim)" fontSize="9" fontWeight="700" textAnchor="start">{maxPrice.toLocaleString()} ₽</text>
                  <text x="12" y="112" fill="var(--color-text-dim)" fontSize="9" fontWeight="700" textAnchor="start">{minPrice.toLocaleString()} ₽</text>
                </svg>
              </div>
            </div>
          </div>

          {/* Right Column: Sentiment & Specifications */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
            {/* Sentiment Breakdown */}
            <div>
              <h3 style={{ fontSize: '14px', fontWeight: 700, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '12px' }}>
                Анализ тональности отзывов
              </h3>
              
              <div className="glass-card" style={{ padding: '16px', background: 'rgba(0,0,0,0.15)', display: 'flex', alignItems: 'center', gap: '24px' }}>
                {/* Radial Gauge */}
                <div style={{ position: 'relative', width: '90px', height: '90px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <svg width="90" height="90" viewBox="0 0 36 36">
                    {/* Circle backgrounds */}
                    <circle cx="18" cy="18" r="15.91" fill="none" stroke="var(--border-glass)" strokeWidth="3" />
                    
                    {/* Green (Positive) */}
                    <circle 
                      cx="18" cy="18" r="15.91" 
                      fill="none" 
                      stroke="var(--color-emerald)" 
                      strokeWidth="3.2" 
                      strokeDasharray={`${sentiment.positive} ${100 - sentiment.positive}`} 
                      strokeDashoffset="25"
                    />

                    {/* Red (Negative) starts at the end of positive */}
                    <circle 
                      cx="18" cy="18" r="15.91" 
                      fill="none" 
                      stroke="var(--color-rose)" 
                      strokeWidth="3.2" 
                      strokeDasharray={`${sentiment.negative} ${100 - sentiment.negative}`} 
                      strokeDashoffset={25 - sentiment.positive - sentiment.neutral}
                    />
                  </svg>
                  
                  <div style={{ position: 'absolute', textAlign: 'center' }}>
                    <div style={{ fontSize: '18px', fontWeight: 800 }}>{sentiment.positive}%</div>
                    <div style={{ fontSize: '9px', fontWeight: 700, color: 'var(--color-text-dim)' }}>ПОЗИТИВ</div>
                  </div>
                </div>

                {/* Bars Legend */}
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', fontWeight: 700, marginBottom: '2px' }}>
                      <span style={{ color: 'var(--color-emerald)' }}>Положительные</span>
                      <span>{sentiment.positive}%</span>
                    </div>
                    <div style={{ height: '6px', background: 'var(--border-glass)', borderRadius: '4px', overflow: 'hidden' }}>
                      <div style={{ width: `${sentiment.positive}%`, height: '100%', background: 'var(--color-emerald)' }} />
                    </div>
                  </div>

                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', fontWeight: 700, marginBottom: '2px' }}>
                      <span style={{ color: 'var(--color-amber)' }}>Нейтральные</span>
                      <span>{sentiment.neutral}%</span>
                    </div>
                    <div style={{ height: '6px', background: 'var(--border-glass)', borderRadius: '4px', overflow: 'hidden' }}>
                      <div style={{ width: `${sentiment.neutral}%`, height: '100%', background: 'var(--color-amber)' }} />
                    </div>
                  </div>

                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', fontWeight: 700, marginBottom: '2px' }}>
                      <span style={{ color: 'var(--color-rose)' }}>Отрицательные</span>
                      <span>{sentiment.negative}%</span>
                    </div>
                    <div style={{ height: '6px', background: 'var(--border-glass)', borderRadius: '4px', overflow: 'hidden' }}>
                      <div style={{ width: `${sentiment.negative}%`, height: '100%', background: 'var(--color-rose)' }} />
                    </div>
                  </div>
                </div>
              </div>

              {/* Review Quotes */}
              <div style={{ marginTop: '14px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
                {sentiment.quotes.positive.slice(0, 1).map((q, idx) => (
                  <div key={idx} style={{ borderLeft: '3px solid var(--color-emerald)', paddingLeft: '12px', fontSize: '12px', fontStyle: 'italic', color: 'var(--color-text-muted)' }}>
                    "{q.text}"
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginTop: '4px', fontSize: '10px', color: 'var(--color-text-dim)', fontStyle: 'normal', fontWeight: 600 }}>
                      <span>⭐ {q.rating}/5</span>
                      <span style={{ display: 'flex', alignItems: 'center', gap: '3px' }}><ThumbsUp size={10} /> +{q.votes_plus}</span>
                    </div>
                  </div>
                ))}
                {sentiment.quotes.negative.slice(0, 1).map((q, idx) => (
                  <div key={idx} style={{ borderLeft: '3px solid var(--color-rose)', paddingLeft: '12px', fontSize: '12px', fontStyle: 'italic', color: 'var(--color-text-muted)' }}>
                    "{q.text}"
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginTop: '4px', fontSize: '10px', color: 'var(--color-text-dim)', fontStyle: 'normal', fontWeight: 600 }}>
                      <span>⭐ {q.rating}/5</span>
                      <span style={{ display: 'flex', alignItems: 'center', gap: '3px' }}><ThumbsUp size={10} /> +{q.votes_plus}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Specifications Sheet */}
            <div>
              <h3 style={{ fontSize: '14px', fontWeight: 700, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '10px' }}>
                Характеристики
              </h3>

              <div style={{ border: '1px solid var(--border-glass)', borderRadius: 'var(--radius-md)', overflow: 'hidden' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                  <tbody>
                    {Object.keys(offer.characteristics).length === 0 ? (
                      <tr>
                        <td colSpan={2} style={{ padding: '12px', color: 'var(--color-text-dim)', textAlign: 'center' }}>
                          Характеристики не нормализованы
                        </td>
                      </tr>
                    ) : (
                      Object.entries(offer.characteristics).map(([key, val], idx) => (
                        <tr 
                          key={key} 
                          style={{ 
                            background: idx % 2 === 0 ? 'rgba(0, 0, 0, 0.1)' : 'transparent',
                            borderBottom: '1px solid var(--border-glass)'
                          }}
                        >
                          <td style={{ padding: '10px 14px', fontWeight: 600, color: 'var(--color-text-muted)', width: '40%' }}>{key}</td>
                          <td style={{ padding: '10px 14px', color: 'var(--color-text)', fontWeight: 500 }}>{val}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
};
