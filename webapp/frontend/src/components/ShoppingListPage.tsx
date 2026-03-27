import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { compareCart } from '../api';
import type { Product, StoreCartResult } from '../types';

// Hebrew chain display names
const CHAIN_NAMES: Record<string, string> = {
  shufersal: 'שופרסל',
  tivtaam: 'טיב טעם',
  carrefour: 'קרפור',
  machsanei: 'מחסני השוק',
  ramilevi: 'רמי לוי',
  yochananof: 'יוחננוף',
  keshet: 'קשת טעמים',
  quik: 'קוויק',
  victory: 'ויקטורי',
  ybitan: 'יינות ביתן',
};

interface ShoppingListPageProps {
  cartItems: Product[];
  onRemoveFromCart: (productId: number) => void;
}

function StoreRow({
  result,
  rank,
}: {
  result: StoreCartResult;
  rank: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const chainLabel = CHAIN_NAMES[result.chain] ?? result.chain;

  return (
    <div
      className={`bg-white rounded-2xl border p-4 transition-shadow hover:shadow-md ${
        result.has_missing
          ? 'border-amber-200'
          : rank === 0
          ? 'border-emerald-300 shadow-emerald-50 shadow-md'
          : 'border-slate-100'
      }`}
    >
      <div
        className="flex items-center justify-between cursor-pointer"
        onClick={() => setExpanded((v) => !v)}
      >
        <div className="flex items-center gap-3">
          {rank === 0 && !result.has_missing && (
            <span className="text-xs bg-emerald-100 text-emerald-700 px-2 py-0.5 rounded-full font-semibold">
              הכי זול
            </span>
          )}
          {result.has_missing && (
            <span className="text-xs bg-amber-100 text-amber-700 px-2 py-0.5 rounded-full font-semibold">
              ⚠ חסרים {result.missing_products.length} מוצרים
            </span>
          )}
          <span className="text-sm font-medium text-slate-600">{chainLabel}</span>
          <span className="text-xs text-slate-400">{result.store_name}</span>
        </div>

        <div className="flex items-center gap-3">
          <span className="text-xl font-bold text-slate-900">
            ₪{result.total_price.toFixed(2)}
          </span>
          <span className="text-slate-300 text-sm">{expanded ? '▲' : '▼'}</span>
        </div>
      </div>

      {/* Warning about missing products */}
      {result.has_missing && (
        <div className="mt-2 text-xs text-amber-600 bg-amber-50 rounded-lg px-3 py-2">
          <strong>שים לב:</strong> המוצרים הבאים לא נמצאו בחנות זו — ההשוואה אינה מלאה:{' '}
          {result.missing_products.join(', ')}
        </div>
      )}

      {/* Expanded breakdown */}
      {expanded && (
        <div className="mt-3 border-t border-slate-100 pt-3 space-y-1">
          {result.items.map((item, i) => (
            <div key={i} className="flex justify-between items-center text-sm">
              <span
                className={
                  item.found ? 'text-slate-700' : 'text-slate-400 line-through'
                }
              >
                {item.ref_name}
                {item.matched_name && item.matched_name !== item.ref_name && (
                  <span className="text-slate-400 text-xs mr-1">
                    ({item.matched_name})
                  </span>
                )}
              </span>
              <span
                className={
                  item.found ? 'font-medium text-slate-800' : 'text-slate-300'
                }
              >
                {item.found ? `₪${item.price!.toFixed(2)}` : 'לא זמין'}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function ShoppingListPage({
  cartItems,
  onRemoveFromCart,
}: ShoppingListPageProps) {
  const cartIds = cartItems.map((p) => ({ product_id: p.id }));
  const enabled = cartItems.length > 0;

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['cart-compare', cartIds.map((i) => i.product_id).join(',')],
    queryFn: () => compareCart(cartIds),
    enabled,
    staleTime: 60_000,
  });

  if (cartItems.length === 0) {
    return (
      <div className="text-center py-20 text-slate-400">
        <div className="text-5xl mb-4">🛒</div>
        <p className="text-lg">הרשימה ריקה</p>
        <p className="text-sm mt-1">חפש מוצרים והוסף אותם לרשימת הקניות</p>
      </div>
    );
  }

  return (
    <div>
      {/* Cart items list */}
      <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-4 mb-4">
        <h2 className="text-base font-semibold text-slate-700 mb-3">
          מוצרים ברשימה ({cartItems.length})
        </h2>
        <div className="space-y-2">
          {cartItems.map((p) => (
            <div
              key={p.id}
              className="flex items-center justify-between text-sm"
            >
              <div className="flex items-center gap-2 min-w-0">
                {p.image_url && (
                  <img
                    src={p.image_url}
                    alt={p.name}
                    className="w-8 h-8 rounded object-contain bg-slate-50"
                    onError={(e) => {
                      (e.target as HTMLImageElement).style.display = 'none';
                    }}
                  />
                )}
                <span className="truncate text-slate-700">{p.name}</span>
              </div>
              <button
                onClick={() => onRemoveFromCart(p.id)}
                className="text-slate-300 hover:text-red-400 transition ml-2 flex-shrink-0 text-lg leading-none"
                title="הסר מהרשימה"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* Compare results */}
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-base font-semibold text-slate-700">
          השוואת מחירים לפי רשת
        </h2>
        <button
          onClick={() => refetch()}
          className="text-xs text-blue-500 hover:text-blue-700 transition"
        >
          רענן
        </button>
      </div>

      {isLoading && (
        <div className="text-center py-8 text-slate-400">
          <div className="inline-block w-6 h-6 border-2 border-blue-300 border-t-blue-600 rounded-full animate-spin mb-2" />
          <p>משווה מחירים...</p>
        </div>
      )}

      {isError && (
        <div className="text-center py-8 text-red-400">
          שגיאה בהשוואה. וודא שהשרת פועל.
        </div>
      )}

      {data && !isLoading && (
        <div className="space-y-3">
          {data.stores.map((store, i) => (
            <StoreRow
              key={`${store.chain}-${store.store_id}`}
              result={store}
              rank={i}
            />
          ))}
        </div>
      )}
    </div>
  );
}
