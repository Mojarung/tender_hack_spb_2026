import React from 'react';
import { Heart, Trash2, ExternalLink, Sparkles, TrendingDown, DollarSign, BarChart2 } from 'lucide-react';
import { ProductOffer, SourceKind } from '../lib/types';

interface FavoritesProps {
  favorites: ProductOffer[];
  onRemove: (offer: ProductOffer) => void;
  onOpenProduct: (offer: ProductOffer) => void;
}

export const Favorites: React.FC<FavoritesProps> = ({
  favorites,
  onRemove,
  onOpenProduct
}) => {
  // Aggregate statistics for wishlist items
  const totalItems = favorites.length;
  const totalPrice = favorites.reduce((acc, curr) => acc + curr.price, 0);
  const avgPrice = totalItems > 0 ? Math.round(totalPrice / totalItems) : 0;
  
  // Potential savings (simulate price drops of 5-10% from original add price)
  const totalSavings = Math.round(totalPrice * 0.06);

  const getSourceLabel = (source: SourceKind) => {
    switch (source) {
      case SourceKind.WB: return 'Wildberries';
      case SourceKind.OZON: return 'Ozon';
      case SourceKind.YA_MARKET: return 'Яндекс Маркет';
      default: return 'Рунет';
    }
  };

  const getSourceIcon = (source: SourceKind) => {
    switch (source) {
      case SourceKind.WB: return '🟣';
      case SourceKind.OZON: return '🔵';
      case SourceKind.YA_MARKET: return '🟡';
      default: return '🟢';
    }
  };

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '40px 20px' }}>
      {/* Title */}
      <div style={{ marginBottom: '32px' }}>
        <h1 style={{ fontSize: '32px', fontWeight: 800, marginBottom: '6px' }}>Мой список избранного</h1>
        <p style={{ color: 'var(--color-text-muted)' }}>Сохраняйте лучшие предложения и следите за динамикой цен</p>
      </div>

      {totalItems === 0 ? (
        <div className="glass-card" style={{ padding: '80px 40px', textAlign: 'center' }}>
          <div style={{ width: '64px', height: '64px', borderRadius: '50%', background: 'var(--color-accent-glow)', display: 'grid', placeItems: 'center', margin: '0 auto 16px auto', color: 'var(--color-accent)' }}>
            <Heart size={28} />
          </div>
          <h3 style={{ fontSize: '18px', fontWeight: 700, marginBottom: '8px' }}>В списке пока ничего нет</h3>
          <p style={{ color: 'var(--color-text-muted)', fontSize: '14px', maxWidth: '380px', margin: '0 auto 24px auto' }}>
            Добавляйте товары с помощью сердечка на карточках во время поиска, чтобы следить за ними.
          </p>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '32px' }}>
          {/* Aggregate Stats Cards */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '20px' }}>
            <div className="glass-card" style={{ padding: '20px', display: 'flex', alignItems: 'center', gap: '16px' }}>
              <div style={{ width: '48px', height: '48px', borderRadius: '12px', background: 'var(--color-accent-glow)', color: 'var(--color-accent)', display: 'grid', placeItems: 'center' }}>
                <Heart size={20} />
              </div>
              <div>
                <span style={{ fontSize: '11px', fontWeight: 700, color: 'var(--color-text-dim)', textTransform: 'uppercase' }}>Всего товаров</span>
                <div style={{ fontSize: '24px', fontWeight: 800 }}>{totalItems} шт</div>
              </div>
            </div>

            <div className="glass-card" style={{ padding: '20px', display: 'flex', alignItems: 'center', gap: '16px' }}>
              <div style={{ width: '48px', height: '48px', borderRadius: '12px', background: 'rgba(16, 185, 129, 0.15)', color: 'var(--color-emerald)', display: 'grid', placeItems: 'center' }}>
                <DollarSign size={20} />
              </div>
              <div>
                <span style={{ fontSize: '11px', fontWeight: 700, color: 'var(--color-text-dim)', textTransform: 'uppercase' }}>Общая ценность</span>
                <div style={{ fontSize: '24px', fontWeight: 800 }}>{totalPrice.toLocaleString()} ₽</div>
              </div>
            </div>

            <div className="glass-card" style={{ padding: '20px', display: 'flex', alignItems: 'center', gap: '16px' }}>
              <div style={{ width: '48px', height: '48px', borderRadius: '12px', background: 'rgba(245, 158, 11, 0.15)', color: 'var(--color-amber)', display: 'grid', placeItems: 'center' }}>
                <BarChart2 size={20} />
              </div>
              <div>
                <span style={{ fontSize: '11px', fontWeight: 700, color: 'var(--color-text-dim)', textTransform: 'uppercase' }}>Средняя цена</span>
                <div style={{ fontSize: '24px', fontWeight: 800 }}>{avgPrice.toLocaleString()} ₽</div>
              </div>
            </div>

            <div className="glass-card" style={{ padding: '20px', display: 'flex', alignItems: 'center', gap: '16px' }}>
              <div style={{ width: '48px', height: '48px', borderRadius: '12px', background: 'rgba(16, 185, 129, 0.15)', color: 'var(--color-emerald)', display: 'grid', placeItems: 'center' }}>
                <TrendingDown size={20} />
              </div>
              <div>
                <span style={{ fontSize: '11px', fontWeight: 700, color: 'var(--color-text-dim)', textTransform: 'uppercase' }}>Сэкономили</span>
                <div style={{ fontSize: '24px', fontWeight: 800, color: 'var(--color-emerald)' }}>~{totalSavings.toLocaleString()} ₽</div>
              </div>
            </div>
          </div>

          {/* List of saved offers */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: '20px' }}>
            {favorites.map(item => (
              <div key={item.url} className="glass-card" style={{ padding: '16px', display: 'flex', gap: '16px', position: 'relative' }}>
                {/* Product image */}
                <div style={{ width: '90px', height: '90px', borderRadius: '8px', overflow: 'hidden', background: '#1f2937', flexShrink: 0 }}>
                  <img 
                    src={item.image || 'https://images.unsplash.com/photo-1510557880182-3d4d3cba35a5?w=500&q=80'} 
                    alt={item.name}
                    style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                  />
                </div>

                {/* Details */}
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'space-between', minWidth: 0 }}>
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '4px' }}>
                      <span style={{ fontSize: '12px' }}>{getSourceIcon(item.source)}</span>
                      <span style={{ fontSize: '10px', fontWeight: 700, color: 'var(--color-text-dim)', textTransform: 'uppercase' }}>
                        {getSourceLabel(item.source)}
                      </span>
                    </div>

                    <h4 
                      onClick={() => onOpenProduct(item)}
                      style={{ fontSize: '13px', fontWeight: 700, color: 'white', cursor: 'pointer', lineHeight: 1.3, marginBottom: '6px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
                    >
                      {item.name}
                    </h4>
                  </div>

                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div style={{ fontSize: '16px', fontWeight: 800 }}>
                      {item.price.toLocaleString()} ₽
                    </div>

                    <div style={{ display: 'flex', gap: '6px' }}>
                      <button 
                        onClick={() => onRemove(item)}
                        style={{
                          width: '28px',
                          height: '28px',
                          borderRadius: '6px',
                          background: 'rgba(244, 63, 94, 0.1)',
                          border: '1px solid rgba(244, 63, 94, 0.2)',
                          color: 'var(--color-rose)',
                          cursor: 'pointer',
                          display: 'grid',
                          placeItems: 'center'
                        }}
                      >
                        <Trash2 size={12} />
                      </button>

                      <a 
                        href={item.url} 
                        target="_blank" 
                        rel="noopener noreferrer"
                        style={{
                          width: '28px',
                          height: '28px',
                          borderRadius: '6px',
                          background: 'rgba(255, 255, 255, 0.05)',
                          border: '1px solid var(--border-glass)',
                          color: 'white',
                          display: 'grid',
                          placeItems: 'center'
                        }}
                      >
                        <ExternalLink size={12} />
                      </a>
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
