import { useState, useCallback } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import SearchPage from './components/SearchPage';
import ShoppingListPage from './components/ShoppingListPage';
import StatusBar from './components/StatusBar';
import type { Product } from './types';
import './index.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

type Tab = 'search' | 'cart';

function Inner() {
  const [activeTab, setActiveTab] = useState<Tab>('search');
  const [cartItems, setCartItems] = useState<Product[]>([]);

  const cartIds = new Set(cartItems.map((p) => p.id));

  const addToCart = useCallback((product: Product) => {
    setCartItems((prev) => {
      if (prev.some((p) => p.id === product.id)) return prev;
      return [...prev, product];
    });
  }, []);

  const removeFromCart = useCallback((productId: number) => {
    setCartItems((prev) => prev.filter((p) => p.id !== productId));
  }, []);

  return (
    <div className="min-h-screen flex flex-col" dir="rtl">
      {/* Header */}
      <header className="bg-white border-b border-slate-100 shadow-sm sticky top-0 z-10">
        <div className="max-w-3xl mx-auto px-4 py-3 flex items-center justify-between">
          <h1 className="text-lg font-bold text-slate-800">
            🛒 השוואת מחירים
          </h1>
          <nav className="flex gap-1 bg-slate-100 rounded-xl p-1">
            <button
              onClick={() => setActiveTab('search')}
              className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-all ${
                activeTab === 'search'
                  ? 'bg-white shadow-sm text-slate-800'
                  : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              חיפוש
            </button>
            <button
              onClick={() => setActiveTab('cart')}
              className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-all relative ${
                activeTab === 'cart'
                  ? 'bg-white shadow-sm text-slate-800'
                  : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              רשימת קניות
              {cartItems.length > 0 && (
                <span className="absolute -top-1 -right-1 w-4 h-4 bg-blue-500 text-white text-xs rounded-full flex items-center justify-center leading-none">
                  {cartItems.length}
                </span>
              )}
            </button>
          </nav>
        </div>
        <StatusBar />
      </header>

      {/* Main content */}
      <main className="flex-1 max-w-3xl mx-auto w-full px-4 py-4">
        {activeTab === 'search' ? (
          <SearchPage cartIds={cartIds} onAddToCart={addToCart} />
        ) : (
          <ShoppingListPage cartItems={cartItems} onRemoveFromCart={removeFromCart} />
        )}
      </main>

      {/* Footer */}
      <footer className="text-center text-xs text-slate-300 py-4 border-t border-slate-100">
        מחירים מעודכנים מהרשתות: שופרסל, טיב טעם, קרפור, רמי לוי, יוחננוף, קשת טעמים, קוויק, ויקטורי, יינות ביתן, מחסני השוק
      </footer>
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Inner />
    </QueryClientProvider>
  );
}
