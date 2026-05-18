import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getChains, searchProducts } from '../api';
import { resolvePreferredChains, subscribePreferredChainsChange } from '../app/chainPreferences';
import ProductPreviewCard from '../components/ProductPreviewCard';
import SearchAutocomplete from '../components/SearchAutocomplete';
import { formatCurrency } from '../lib/format';
import { parseSearchQuantity } from '../lib/queryQuantity';

function useDebouncedValue<T>(value: T, delay: number) {
  const [debouncedValue, setDebouncedValue] = useState(value);

  useEffect(() => {
    const timeout = window.setTimeout(() => setDebouncedValue(value), delay);
    return () => window.clearTimeout(timeout);
  }, [delay, value]);

  return debouncedValue;
}

const SEARCH_SCROLL_KEY = 'supermarket.search.scrollY';

export default function SearchPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const initialQuery = searchParams.get('q') ?? '';
  const initialOffset = Number(searchParams.get('offset') ?? 0);
  const [query, setQuery] = useState(initialQuery);
  const [submittedQuery, setSubmittedQuery] = useState(initialQuery);
  const [offset, setOffset] = useState(Number.isFinite(initialOffset) && initialOffset > 0 ? initialOffset : 0);
  const [selectedChains, setSelectedChains] = useState<string[]>([]);
  const [autocompleteHiddenForQuery, setAutocompleteHiddenForQuery] = useState<string | null>(null);

  useEffect(() => {
    const savedScrollY = Number(window.sessionStorage.getItem(SEARCH_SCROLL_KEY) ?? 0);
    if (Number.isFinite(savedScrollY) && savedScrollY > 0) {
      window.requestAnimationFrame(() => window.scrollTo({ top: savedScrollY }));
    }
    return () => {
      window.sessionStorage.setItem(SEARCH_SCROLL_KEY, String(window.scrollY));
    };
  }, []);

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

  const debouncedQuery = useDebouncedValue(query, 250);
  const trimmedQuery = query.trim();
  const parsedCurrentQuery = parseSearchQuantity(query);
  const parsedSubmittedQuery = parseSearchQuantity(submittedQuery);
  const searchQueryText = parsedSubmittedQuery.cleanedQuery;
  const quantityParams = parsedSubmittedQuery.quantity
    ? `?qty=${encodeURIComponent(String(parsedSubmittedQuery.quantity.value))}&dim=${encodeURIComponent(parsedSubmittedQuery.quantity.dimension)}`
    : '';
  const currentQuantityParams = parsedCurrentQuery.quantity
    ? `?qty=${encodeURIComponent(String(parsedCurrentQuery.quantity.value))}&dim=${encodeURIComponent(parsedCurrentQuery.quantity.dimension)}`
    : '';
  const showAutocomplete = autocompleteHiddenForQuery !== debouncedQuery.trim();
  const readyForSearch = searchQueryText.trim().length >= 3;

  useEffect(() => {
    const nextQuery = searchParams.get('q') ?? '';
    const nextOffsetRaw = Number(searchParams.get('offset') ?? 0);
    const nextOffset = Number.isFinite(nextOffsetRaw) && nextOffsetRaw > 0 ? nextOffsetRaw : 0;

    setQuery(nextQuery);
    setSubmittedQuery(nextQuery);
    setOffset(nextOffset);
  }, [searchParams]);

  const updateSearchUrl = (nextQuery: string, nextOffset: number) => {
    const nextParams = new URLSearchParams();
    if (nextQuery) {
      nextParams.set('q', nextQuery);
    }
    if (nextOffset > 0) {
      nextParams.set('offset', String(nextOffset));
    }
    setSearchParams(nextParams, { replace: true });
  };

  const resultsQuery = useQuery({
    queryKey: ['product-search', searchQueryText, offset, selectedChains.join(',')],
    queryFn: () => searchProducts(searchQueryText, 20, offset, selectedChains),
    enabled: readyForSearch,
  });

  const totalPages = useMemo(() => {
    if (!resultsQuery.data) {
      return 0;
    }
    return Math.max(1, Math.ceil(resultsQuery.data.total / 20));
  }, [resultsQuery.data]);
  const genericGroups = resultsQuery.data?.generic_groups ?? [];

  return (
    <>
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1.1fr)_20rem]">
      <section className="space-y-4">
        <div className="rounded-[34px] border border-white/80 bg-white/95 p-5 shadow-[0_20px_60px_-36px_rgba(15,23,42,0.35)] sm:p-6">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-sky-600">Smart search</p>
              <h2 className="mt-2 text-2xl font-black text-slate-900">חפש/י מוצרים להשוואה</h2>
              <p className="mt-2 text-sm leading-6 text-slate-500 sm:text-base">
                כתבו לפחות 3 תווים כדי לקבל הצעות. לחיצה על חיפוש תציג את כל המוצרים התואמים עם המחיר הזול ביותר לכל מוצר.
              </p>
              <p className="mt-2 text-sm text-slate-500">
                סינון רשתות פעיל מתוך הגדרות: {selectedChains.length.toLocaleString('he-IL')}
              </p>
            </div>
          </div>

          <form
            className="mt-5 space-y-3"
            onSubmit={(event) => {
              event.preventDefault();
              const nextQuery = query.trim();
              setSubmittedQuery(nextQuery);
              setOffset(0);
              setAutocompleteHiddenForQuery(nextQuery);
              updateSearchUrl(nextQuery, 0);
            }}
          >
            <div className="flex flex-col gap-3 sm:flex-row">
              <input
                type="search"
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setAutocompleteHiddenForQuery(null);
                }}
                onFocus={() => {
                  if (trimmedQuery !== autocompleteHiddenForQuery) {
                    setAutocompleteHiddenForQuery(null);
                  }
                }}
                placeholder="למשל: חלב, ביצים, קוטג׳"
                className="min-h-14 flex-1 rounded-[24px] border border-slate-200 bg-slate-50 px-5 text-base outline-none transition focus:border-sky-300 focus:bg-white"
              />
              <button
                type="submit"
                className="min-h-14 rounded-[24px] bg-slate-900 px-6 text-base font-black text-white transition hover:bg-slate-800"
              >
                חיפוש
              </button>
            </div>

            {query.trim().length > 0 && query.trim().length < 3 && (
              <p className="rounded-2xl bg-amber-50 px-4 py-3 text-sm font-medium text-amber-700">
                צריך לפחות 3 תווים לפני שנשלח חיפוש לשרת.
              </p>
            )}

            {showAutocomplete && (
              <SearchAutocomplete
                query={parseSearchQuantity(debouncedQuery).cleanedQuery}
                chains={selectedChains}
                onSelect={(productId) => {
                  setAutocompleteHiddenForQuery(query.trim());
                  navigate(`/products/${productId}${currentQuantityParams}`);
                }}
              />
            )}
          </form>
        </div>

        {resultsQuery.isLoading && (
          <div className="rounded-[30px] border border-dashed border-slate-200 bg-white/90 px-5 py-12 text-center text-slate-500 shadow-sm">
            מחפש מוצרים...
          </div>
        )}

        {readyForSearch && resultsQuery.data && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-slate-500">תוצאות עבור</p>
                <h3 className="text-xl font-black text-slate-900">{submittedQuery}</h3>
              </div>
              <div className="rounded-full bg-white px-4 py-2 text-sm font-semibold text-slate-600 shadow-sm">
                {resultsQuery.data.total.toLocaleString('he-IL')} מוצרים
              </div>
            </div>

            {genericGroups.length > 0 && (
              <section className="space-y-3 rounded-[30px] border border-emerald-100 bg-emerald-50/70 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-[0.22em] text-emerald-700">Comparable groups</p>
                    <h4 className="text-lg font-black text-slate-900">מוצרים כלליים להשוואה</h4>
                  </div>
                  <span className="rounded-full bg-white px-3 py-1 text-xs font-bold text-emerald-700">
                    לפי גודל וסוג זהים
                  </span>
                </div>
                <div className="grid gap-3 xl:grid-cols-2">
                  {genericGroups.map((group) => (
                    <button
                      key={group.key}
                      type="button"
                      onClick={() => navigate(`/groups/${encodeURIComponent(group.key)}${quantityParams}`)}
                      className="rounded-[24px] border border-white bg-white/95 p-4 text-right shadow-sm transition hover:-translate-y-0.5 hover:shadow-md"
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
              <div className="rounded-[30px] border border-dashed border-slate-200 bg-white/90 px-5 py-12 text-center text-slate-500 shadow-sm">
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
                    updateSearchUrl(submittedQuery, nextOffset);
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
                    updateSearchUrl(submittedQuery, nextOffset);
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

      <aside className="space-y-4">
        <div className="rounded-[34px] border border-sky-100 bg-sky-50/90 p-5 shadow-[0_16px_40px_-34px_rgba(14,165,233,0.4)]">
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-sky-700">Flow</p>
          <ol className="mt-4 space-y-4 text-sm text-slate-700">
            <li className="rounded-2xl bg-white px-4 py-3 shadow-sm">
              1. מצאי מוצר מדויק מתוך ההצעות האוטומטיות.
            </li>
            <li className="rounded-2xl bg-white px-4 py-3 shadow-sm">
              2. פתחי את דף ההשוואה כדי לראות מחירים לכל רשת.
            </li>
            <li className="rounded-2xl bg-white px-4 py-3 shadow-sm">
              3. הוסיפי לרשימה קיימת או חדשה והשווי סל מלא.
            </li>
          </ol>
        </div>

        <div className="rounded-[34px] border border-white/80 bg-white/95 p-5 shadow-sm">
          <p className="text-sm font-black text-slate-900">למה צריך 3 תווים?</p>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            כדי להוריד עומס מיותר על השרת, למנוע ספאם בחיפושים קצרים מדי, ולשמור על חוויה מהירה במיוחד במובייל.
          </p>
        </div>
      </aside>
    </div>
    </>
  );
}
