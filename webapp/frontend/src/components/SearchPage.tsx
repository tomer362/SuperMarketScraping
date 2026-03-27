import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { searchProducts } from '../api';
import type { Product } from '../types';
import ProductCard from './ProductCard';

interface SearchPageProps {
  cartIds: Set<number>;
  onAddToCart: (product: Product) => void;
}

function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}

export default function SearchPage({ cartIds, onAddToCart }: SearchPageProps) {
  const [query, setQuery] = useState('');
  const [chain, setChain] = useState('');
  const [offset, setOffset] = useState(0);
  const PAGE_SIZE = 30;

  const debouncedQuery = useDebounce(query, 350);

  // Reset pagination on query change
  useEffect(() => {
    setOffset(0);
  }, [debouncedQuery, chain]);

  const { data, isLoading, isError } = useQuery({
    queryKey: ['search', debouncedQuery, chain, offset],
    queryFn: () => searchProducts(debouncedQuery, PAGE_SIZE, offset, chain || undefined),
    placeholderData: (prev) => prev,
    staleTime: 30_000,
  });

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 0;
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  const CHAINS = [
    { value: '', label: 'כל הרשתות' },
    { value: 'shufersal', label: 'שופרסל' },
    { value: 'tivtaam', label: 'טיב טעם' },
    { value: 'carrefour', label: 'קרפור' },
    { value: 'machsanei', label: 'מחסני השוק' },
    { value: 'ramilevi', label: 'רמי לוי' },
    { value: 'yochananof', label: 'יוחננוף' },
    { value: 'keshet', label: 'קשת טעמים' },
    { value: 'quik', label: 'קוויק' },
    { value: 'victory', label: 'ויקטורי' },
    { value: 'ybitan', label: 'יינות ביתן' },
  ];

  return (
    <div>
      {/* Search bar */}
      <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-4 mb-4">
        <div className="flex gap-3 flex-wrap">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="חפש מוצר... (למשל: ביצים, חלב, קוטג׳)"
            className="flex-1 min-w-0 border border-slate-200 rounded-xl px-4 py-2.5 text-base outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent transition text-right"
            dir="rtl"
            autoFocus
          />
          <select
            value={chain}
            onChange={(e) => setChain(e.target.value)}
            className="border border-slate-200 rounded-xl px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-400 bg-white cursor-pointer"
            dir="rtl"
          >
            {CHAINS.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Status */}
      {isLoading && (
        <div className="text-center py-8 text-slate-400">
          <div className="inline-block w-6 h-6 border-2 border-blue-300 border-t-blue-600 rounded-full animate-spin mb-2" />
          <p>מחפש...</p>
        </div>
      )}

      {isError && (
        <div className="text-center py-8 text-red-400">
          שגיאה בחיפוש. וודא שהשרת פועל.
        </div>
      )}

      {data && !isLoading && (
        <>
          <p className="text-sm text-slate-400 mb-3 text-right pr-1">
            {data.total.toLocaleString('he-IL')} מוצרים נמצאו
            {debouncedQuery && ` עבור "${debouncedQuery}"`}
          </p>

          {data.products.length === 0 && (
            <div className="text-center py-12 text-slate-400">
              לא נמצאו מוצרים. נסה חיפוש אחר.
            </div>
          )}

          <div className="space-y-2">
            {data.products.map((p) => (
              <ProductCard
                key={p.id}
                product={p}
                onAddToCart={onAddToCart}
                inCart={cartIds.has(p.id)}
              />
            ))}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-3 mt-6">
              <button
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                disabled={offset === 0}
                className="px-4 py-2 rounded-xl bg-white border border-slate-200 text-sm disabled:opacity-40 hover:bg-slate-50 transition"
              >
                → הקודם
              </button>
              <span className="text-sm text-slate-500">
                עמוד {currentPage} מתוך {totalPages}
              </span>
              <button
                onClick={() => setOffset(offset + PAGE_SIZE)}
                disabled={currentPage >= totalPages}
                className="px-4 py-2 rounded-xl bg-white border border-slate-200 text-sm disabled:opacity-40 hover:bg-slate-50 transition"
              >
                הבא ←
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
