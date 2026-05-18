import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getChains, searchProducts } from '../api';
import { resolvePreferredChains, subscribePreferredChainsChange } from '../app/chainPreferences';
import ProductPreviewCard from '../components/ProductPreviewCard';
import { formatCurrency } from '../lib/format';
import { parseSearchQuantity } from '../lib/queryQuantity';

const SEARCH_SCROLL_KEY = 'supermarket.search.scrollY';

export default function SearchPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const query = searchParams.get('q') ?? '';
  const initialOffset = Number(searchParams.get('offset') ?? 0);
  const [offset, setOffset] = useState(Number.isFinite(initialOffset) && initialOffset > 0 ? initialOffset : 0);
  const [selectedChains, setSelectedChains] = useState<string[]>([]);

  useEffect(() => {
    const savedScrollY = Number(window.sessionStorage.getItem(SEARCH_SCROLL_KEY) ?? 0);
    if (Number.isFinite(savedScrollY) && savedScrollY > 0) {
      window.requestAnimationFrame(() => window.scrollTo({ top: savedScrollY }));
    }
    return () => {
      window.sessionStorage.setItem(SEARCH_SCROLL_KEY, String(window.scrollY));
    };
  }, []);

  useEffect(() => {
    const nextOffsetRaw = Number(searchParams.get('offset') ?? 0);
    setOffset(Number.isFinite(nextOffsetRaw) && nextOffsetRaw > 0 ? nextOffsetRaw : 0);
  }, [searchParams]);

  const chainsQuery = useQuery({ queryKey: ['chains'], queryFn: getChains });
  const activeChains = useMemo(
    () => (chainsQuery.data ?? []).filter((chain) => chain.enabled),
    [chainsQuery.data],
  );

  useEffect(() => {
    if (activeChains.length === 0) {
      setSelectedChains([]);
      return;
    }
    const syncSelection = () => {
      const availableKeys = activeChains.map((chain) => chain.chain);
      setSelectedChains(resolvePreferredChains(availableKeys));
    };
    syncSelection();
    return subscribePreferredChainsChange(syncSelection);
  }, [activeChains]);

  const parsedQuery = parseSearchQuantity(query);
  const searchQueryText = parsedQuery.cleanedQuery;
  const quantityParams = parsedQuery.quantity
    ? `?qty=${encodeURIComponent(String(parsedQuery.quantity.value))}&dim=${encodeURIComponent(parsedQuery.quantity.dimension)}`
    : '';
  const readyForSearch = searchQueryText.trim().length >= 3;

  const updateSearchUrl = (nextOffset: number) => {
    const nextParams = new URLSearchParams();
    if (query) {
      nextParams.set('q', query);
    }
    if (nextOffset > 0) {
      nextParams.set('offset', String(nextOffset));
    }
    setSearchParams(nextParams, { replace: true });
  };

  const resultsQuery = useQuery({
    queryKey: ['product-search', searchQueryText, offset, selectedChains.join(',')],
    queryFn: () => searchProducts(searchQueryText, 20, offset, selectedChains),
    enabled: readyForSearch && !chainsQuery.isLoading,
  });

  const totalPages = useMemo(() => {
    if (!resultsQuery.data) {
      return 0;
    }
    return Math.max(1, Math.ceil(resultsQuery.data.total / 20));
  }, [resultsQuery.data]);
  const genericGroups = resultsQuery.data?.generic_groups ?? [];

  return (
    <div className="mx-auto max-w-5xl">
      <section className="space-y-4">
        {query.trim().length > 0 && query.trim().length < 3 && (
          <p className="rounded-2xl bg-amber-50 px-4 py-3 text-sm font-medium text-amber-700">
            צריך לפחות 3 תווים לפני שנשלח חיפוש לשרת.
          </p>
        )}

        {resultsQuery.isLoading && (
          <div className="rounded-[24px] border border-dashed border-slate-200 bg-white/90 px-5 py-12 text-center text-slate-500 shadow-sm">
            מחפש מוצרים...
          </div>
        )}

        {readyForSearch && resultsQuery.data && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-slate-500">תוצאות עבור</p>
                <h3 className="text-2xl font-black text-slate-900">{query}</h3>
              </div>
              <div className="rounded-full bg-white px-4 py-2 text-sm font-semibold text-slate-600 shadow-sm">
                {resultsQuery.data.total.toLocaleString('he-IL')} מוצרים
              </div>
            </div>

            {genericGroups.length > 0 && (
              <section className="space-y-3 rounded-[24px] border border-emerald-100 bg-emerald-50/70 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-[0.22em] text-emerald-700">Comparable groups</p>
                    <h4 className="text-lg font-black text-slate-900">מוצרים כלליים להשוואה</h4>
                  </div>
                  <span className="rounded-full bg-white px-3 py-1 text-xs font-bold text-emerald-700">
                    לפי גודל וסוג זהים
                  </span>
                </div>
                <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(min(100%,22rem),1fr))]">
                  {genericGroups.map((group) => (
                    <button
                      key={group.key}
                      type="button"
                      onClick={() => navigate(`/groups/${encodeURIComponent(group.key)}${quantityParams}`)}
                      className="rounded-[20px] border border-white bg-white/95 p-4 text-right shadow-sm transition hover:-translate-y-0.5 hover:shadow-md"
                    >
                      <p className="text-base font-black text-slate-900">{group.label}</p>
                      <p className="mt-1 text-sm text-slate-500">
                        {group.chain_count} רשתות · {group.offer_count} הצעות תואמות
                      </p>
                      <div className="mt-3 flex items-end justify-between gap-3">
                        <span className="rounded-full bg-emerald-100 px-3 py-1 text-xs font-bold text-emerald-700">
                          השווה מחירים
                        </span>
                        <span className="text-lg font-black text-emerald-700">
                          {group.cheapest_price != null ? formatCurrency(group.cheapest_price) : 'מחיר משתנה'}
                        </span>
                      </div>
                    </button>
                  ))}
                </div>
              </section>
            )}

            {resultsQuery.data.products.length === 0 ? (
              <div className="rounded-[24px] border border-dashed border-slate-200 bg-white/90 px-5 py-12 text-center text-slate-500 shadow-sm">
                לא מצאנו מוצרים תואמים. נסו מונח אחר.
              </div>
            ) : (
              <div className="grid gap-4 xl:grid-cols-2">
                {resultsQuery.data.products.map((product) => (
                  <ProductPreviewCard key={product.id} product={product} detailParams={quantityParams} />
                ))}
              </div>
            )}

            {totalPages > 1 && (
              <div className="flex items-center justify-center gap-3 rounded-full bg-white/95 px-4 py-3 shadow-sm">
                <button
                  type="button"
                  onClick={() => {
                    const nextOffset = Math.max(0, offset - 20);
                    setOffset(nextOffset);
                    updateSearchUrl(nextOffset);
                  }}
                  disabled={offset === 0}
                  className="rounded-full border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 disabled:opacity-40"
                >
                  הקודם
                </button>
                <span className="text-sm text-slate-500">
                  עמוד {Math.floor(offset / 20) + 1} מתוך {totalPages}
                </span>
                <button
                  type="button"
                  onClick={() => {
                    const nextOffset = offset + 20;
                    setOffset(nextOffset);
                    updateSearchUrl(nextOffset);
                  }}
                  disabled={Math.floor(offset / 20) + 1 >= totalPages}
                  className="rounded-full border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 disabled:opacity-40"
                >
                  הבא
                </button>
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
