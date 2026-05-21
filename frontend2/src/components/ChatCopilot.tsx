import React, { useState, useRef, useEffect } from 'react';
import { MessageSquare, Send, Sparkles, X, ChevronDown, ChevronUp, Bot, User } from 'lucide-react';
import { api } from '../lib/api';
import { SourceKind } from '../lib/types';

interface Message {
  role: 'user' | 'assistant';
  content: string;
  toolCalls?: Array<{ name: string; result_keys: any }>;
}

export const ChatCopilot: React.FC<{ isDemoMode: boolean }> = ({ isDemoMode }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState<Message[]>([
    {
      role: 'assistant',
      content: 'Привет! Я твой интеллектуальный помощник PricePulse. Я могу сравнить цены на товары, проанализировать отзывы и подсказать лучшую сделку. Задай любой вопрос!'
    }
  ]);
  const [isLoading, setIsLoading] = useState(false);
  const [expandedToolsIdx, setExpandedToolsIdx] = useState<number | null>(null);
  
  const chatEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (chatEndRef.current) {
      chatEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages, isLoading]);

  const handleSend = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    const userMessage = input.trim();
    setInput('');
    setMessages(prev => [...prev, { role: 'user', content: userMessage }]);
    setIsLoading(true);

    if (isDemoMode) {
      // ────────────── SIMULATED BOT (DEMO MODE) ──────────────
      setTimeout(() => {
        let reply = '';
        let toolCalls: any[] = [];
        
        const q = userMessage.toLowerCase();
        if (q.includes('iphone') || q.includes('айфон')) {
          toolCalls = [
            { name: 'search_marketplace', result_keys: { query: 'iphone 15 128gb', sources: ['wb', 'ozon', 'ya_market'] } },
            { name: 'calculate_best_deal', result_keys: { top_deal_score: 98.4, top_deal_url: 'https://ozon.ru/...' } }
          ];
          reply = 'Я изучил предложения по iPhone 15 128GB. Лучшая сделка найдена на **Ozon** у продавца *Ozon Казахстан* за **66 490 ₽** (Best-Deal Score: **98.4**). На **Яндекс Маркете** цены начинаются от 68 990 ₽. Хочешь посмотреть историю цен или отзывы?';
        } else if (q.includes('macbook') || q.includes('макбук')) {
          toolCalls = [
            { name: 'search_marketplace', result_keys: { query: 'macbook air m3', sources: ['wb', 'ozon', 'ya_market'] } },
            { name: 'get_sentiment_breakdown', result_keys: { source: 'wb', reviews_analyzed: 45 } }
          ];
          reply = 'По запросу MacBook Air M3 собрано 5 предложений. Самый дешевый вариант обнаружен на **Ozon** за **106 990 ₽**. Отзывы о модели на **Wildberries** исключительно положительные (92% позитива): пользователи особенно хвалят бесшумность и цвет Midnight.';
        } else {
          toolCalls = [
            { name: 'search_web_runet', result_keys: { query: userMessage } }
          ];
          reply = `Я проверил наличие товара по вашему запросу "${userMessage}". В данный момент на маркетплейсах обнаружены единичные предложения. Рекомендую уточнить характеристики или бренд для более точного сравнения цен.`;
        }

        setMessages(prev => [...prev, {
          role: 'assistant',
          content: reply,
          toolCalls
        }]);
        setIsLoading(false);
      }, 1500);

    } else {
      // ────────────── LIVE GEMMA 4 BOT ──────────────
      try {
        const res = await api.chat(userMessage);
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: res.reply,
          toolCalls: res.tool_calls
        }]);
      } catch (err: any) {
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: `Ошибка подключения к AI-бэкенду: ${err.message || err}. Проверьте статус контейнера Gemma 4 в панели Observability.`
        }]);
      } finally {
        setIsLoading(false);
      }
    }
  };

  const getToolDisplayName = (name: string) => {
    switch (name) {
      case 'search_marketplace': return 'Поиск по маркетплейсам';
      case 'calculate_best_deal': return 'Вычисление формулы лучшей цены';
      case 'get_sentiment_breakdown': return 'Сбор тональности отзывов';
      case 'search_web_runet': return 'Поиск в открытом Рунете (SearXNG)';
      default: return name;
    }
  };

  return (
    <>
      {/* Floating Trigger Button */}
      {!isOpen && (
        <button className="chat-widget-trigger" onClick={() => setIsOpen(true)}>
          <MessageSquare size={24} />
        </button>
      )}

      {/* Floating Chat Panel */}
      {isOpen && (
        <div className="chat-panel glass-card" style={{ border: '1px solid var(--border-glass-hover)', overflow: 'hidden' }}>
          {/* Panel Header */}
          <div style={{
            background: 'linear-gradient(135deg, var(--color-accent), #7c3aed)',
            padding: '12px 16px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            color: 'white'
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Sparkles size={16} />
              <span style={{ fontWeight: 700, fontSize: '14px', letterSpacing: '-0.01em' }}>AI Ассистент PricePulse</span>
            </div>
            <button 
              onClick={() => setIsOpen(false)}
              style={{ background: 'transparent', border: 'none', color: 'white', cursor: 'pointer', opacity: 0.8 }}
            >
              <X size={18} />
            </button>
          </div>

          {/* Messages Body */}
          <div className="chat-messages" style={{
            flex: 1,
            padding: '16px',
            background: 'rgba(3, 7, 18, 0.4)',
            display: 'flex',
            flexDirection: 'column',
            gap: '12px'
          }}>
            {messages.map((msg, idx) => (
              <div 
                key={idx} 
                style={{ 
                  display: 'flex', 
                  flexDirection: 'column',
                  alignItems: msg.role === 'user' ? 'flex-end' : 'flex-start',
                  maxWidth: '85%' 
                }}
              >
                {/* Bubble Container */}
                <div style={{
                  background: msg.role === 'user' ? 'var(--color-accent)' : 'var(--surface-solid)',
                  border: msg.role === 'user' ? 'none' : '1px solid var(--border-glass)',
                  color: 'var(--color-text)',
                  padding: '10px 14px',
                  borderRadius: msg.role === 'user' ? '14px 14px 2px 14px' : '14px 14px 14px 2px',
                  fontSize: '13px',
                  lineHeight: 1.4,
                  whiteSpace: 'pre-line'
                }}>
                  {msg.content}
                </div>

                {/* Show Tool audit logs if present */}
                {msg.toolCalls && msg.toolCalls.length > 0 && (
                  <div style={{ width: '100%', marginTop: '6px' }}>
                    <button
                      onClick={() => setExpandedToolsIdx(expandedToolsIdx === idx ? null : idx)}
                      style={{
                        background: 'transparent',
                        border: 'none',
                        color: 'var(--color-accent)',
                        fontSize: '11px',
                        fontWeight: 700,
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '4px'
                      }}
                    >
                      {expandedToolsIdx === idx ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                      Лог инструментов ({msg.toolCalls.length})
                    </button>
                    
                    {expandedToolsIdx === idx && (
                      <div style={{
                        marginTop: '4px',
                        background: 'rgba(0,0,0,0.3)',
                        border: '1px solid var(--border-glass)',
                        borderRadius: '6px',
                        padding: '6px 10px',
                        fontSize: '10px',
                        fontFamily: 'monospace',
                        color: 'var(--color-text-dim)'
                      }}>
                        {msg.toolCalls.map((tool, tIdx) => (
                          <div key={tIdx} style={{ marginBottom: tIdx < msg.toolCalls!.length - 1 ? '6px' : 0 }}>
                            <div style={{ color: 'var(--color-emerald)', fontWeight: 700 }}>⚙️ {getToolDisplayName(tool.name)}</div>
                            <div style={{ paddingLeft: '8px', wordBreak: 'break-all' }}>
                              result: {JSON.stringify(tool.result_keys)}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}

            {/* Spinner indicator when loading */}
            {isLoading && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '8px 12px', background: 'var(--surface-solid)', border: '1px solid var(--border-glass)', borderRadius: '12px', width: 'fit-content' }}>
                <span className="pulse-dot" style={{ width: '6px', height: '6px' }}></span>
                <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--color-text-muted)' }}>AI думает и скрейпит...</span>
              </div>
            )}
            
            <div ref={chatEndRef} />
          </div>

          {/* Send Input Bar */}
          <form 
            onSubmit={handleSend}
            style={{
              padding: '12px',
              borderTop: '1px solid var(--border-glass)',
              background: 'var(--surface-solid)',
              display: 'flex',
              gap: '8px'
            }}
          >
            <input 
              type="text" 
              value={input}
              onChange={e => setInput(e.target.value)}
              placeholder="Сравни цены на iPhone..."
              disabled={isLoading}
              style={{
                flex: 1,
                background: 'rgba(0,0,0,0.2)',
                border: '1px solid var(--border-glass)',
                borderRadius: '8px',
                padding: '8px 12px',
                color: 'white',
                fontSize: '13px',
                outline: 'none'
              }}
            />
            <button 
              type="submit"
              disabled={!input.trim() || isLoading}
              style={{
                width: '36px',
                height: '36px',
                borderRadius: '8px',
                background: 'var(--color-accent)',
                border: 'none',
                color: 'white',
                display: 'grid',
                placeItems: 'center',
                cursor: 'pointer',
                opacity: input.trim() ? 1 : 0.5
              }}
            >
              <Send size={16} />
            </button>
          </form>
        </div>
      )}
    </>
  );
};
