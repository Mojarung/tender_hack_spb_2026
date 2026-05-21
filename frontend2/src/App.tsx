import React, { useState, useEffect } from 'react';
import { ShaderBackground } from './components/ShaderBackground';
import { SearchDashboard } from './screens/SearchDashboard';
import { Favorites } from './screens/Favorites';
import { Observability } from './screens/Observability';
import { ChatCopilot } from './components/ChatCopilot';
import { ProductModal } from './components/ProductModal';
import { ProductOffer } from './lib/types';
import { Search, Heart, Shield, Radio, Sun, Moon } from 'lucide-react';

export const App: React.FC = () => {
  const [currentTab, setCurrentTab] = useState<'search' | 'favorites' | 'observability'>('search');
  const [isDemoMode, setIsDemoMode] = useState(true);
  const [favorites, setFavorites] = useState<ProductOffer[]>([]);
  const [selectedOffer, setSelectedOffer] = useState<ProductOffer | null>(null);
  const [theme, setTheme] = useState<'dark' | 'light'>('dark');

  // Load favorites from local storage on mount
  useEffect(() => {
    const saved = localStorage.getItem('pp.favorites2');
    if (saved) {
      try {
        setFavorites(JSON.parse(saved));
      } catch (e) {
        console.error('Failed to parse favorites', e);
      }
    }
  }, []);

  // Sync favorites with local storage
  const syncFavorites = (newFavs: ProductOffer[]) => {
    setFavorites(newFavs);
    localStorage.setItem('pp.favorites2', JSON.stringify(newFavs));
  };

  const handleAddRemoveFavorite = (offer: ProductOffer) => {
    const isAlreadyFav = favorites.some(f => f.url === offer.url);
    if (isAlreadyFav) {
      const filtered = favorites.filter(f => f.url !== offer.url);
      syncFavorites(filtered);
    } else {
      const updated = [...favorites, offer];
      syncFavorites(updated);
    }
  };

  const handleRemoveFavoriteOnly = (offer: ProductOffer) => {
    const filtered = favorites.filter(f => f.url !== offer.url);
    syncFavorites(filtered);
  };

  const toggleTheme = () => {
    const newTheme = theme === 'dark' ? 'light' : 'dark';
    setTheme(newTheme);
    document.documentElement.setAttribute('data-theme', newTheme);
  };

  return (
    <div className="app-container">
      {/* Dynamic 60fps fragment WebGL Shader canvas on background */}
      <ShaderBackground />

      {/* Modern Header Section */}
      <header className="header">
        <a href="#" className="header-logo" onClick={() => setCurrentTab('search')}>
          <div className="pulse-dot" />
          <span>PricePulse</span>
        </a>

        {/* Tab Navigation Controls */}
        <nav className="header-nav">
          <button 
            className={`nav-tab ${currentTab === 'search' ? 'active' : ''}`}
            onClick={() => setCurrentTab('search')}
          >
            <Search size={14} /> Поиск
          </button>
          <button 
            className={`nav-tab ${currentTab === 'favorites' ? 'active' : ''}`}
            onClick={() => setCurrentTab('favorites')}
          >
            <Heart size={14} /> Избранное {favorites.length > 0 && <span style={{ background: 'var(--color-rose)', color: 'white', padding: '1px 6px', borderRadius: '10px', fontSize: '9px', fontWeight: 800 }}>{favorites.length}</span>}
          </button>
          <button 
            className={`nav-tab ${currentTab === 'observability' ? 'active' : ''}`}
            onClick={() => setCurrentTab('observability')}
          >
            <Radio size={14} /> Телеметрия
          </button>
        </nav>

        {/* Right side Settings & Demo Mode toggle switch */}
        <div className="header-controls">
          {/* Light/Dark Toggle */}
          <button className="btn-icon" onClick={toggleTheme}>
            {theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
          </button>

          {/* Demo/Live Switch */}
          <div className="toggle-container">
            <span className="toggle-label" style={{ color: isDemoMode ? 'var(--color-amber)' : 'var(--color-emerald)' }}>
              {isDemoMode ? 'DEMO' : 'LIVE API'}
            </span>
            <label className="toggle-switch">
              <input 
                type="checkbox" 
                checked={!isDemoMode} 
                onChange={() => setIsDemoMode(!isDemoMode)} 
              />
              <span className="toggle-slider"></span>
            </label>
          </div>
        </div>
      </header>

      {/* Render Selected Tabs Screens */}
      <div style={{ flex: 1 }}>
        {currentTab === 'search' && (
          <SearchDashboard 
            isDemoMode={isDemoMode}
            onOpenProduct={setSelectedOffer}
            onAddFavorite={handleAddRemoveFavorite}
            favorites={favorites}
          />
        )}
        {currentTab === 'favorites' && (
          <Favorites 
            favorites={favorites}
            onRemove={handleRemoveFavoriteOnly}
            onOpenProduct={setSelectedOffer}
          />
        )}
        {currentTab === 'observability' && (
          <Observability isDemoMode={isDemoMode} />
        )}
      </div>

      {/* Floating chatbot assistant */}
      <ChatCopilot isDemoMode={isDemoMode} />

      {/* Animated Modal drawer detail */}
      {selectedOffer && (
        <ProductModal 
          offer={selectedOffer}
          onClose={() => setSelectedOffer(null)}
          onAddFavorite={handleAddRemoveFavorite}
          isFavorite={favorites.some(f => f.url === selectedOffer.url)}
        />
      )}
    </div>
  );
};

export default App;
