import { useQuery } from '@tanstack/react-query';
import { getSuggestions } from '../api';
import { formatCurrency } from '../lib/format';

interface SearchAutocompleteProps {
  query: string;
  onSelect: (productId: number) => void;
}

export default function SearchAutocomplete({ query, onSelect }: SearchAutocompleteProps) {
  const enabled = query.trim().length >= 3;
  const suggestionsQuery = useQuery({
    queryKey: ['suggestions', query],
    queryFn: () => getSuggestions(query),
    enabled,
  });

  if (!enabled) {
    return null;
  }

  if (suggestionsQuery.isLoading) {
    return (
      <div className="rounded-[24px] border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500 shadow-sm">
        טוען הצעות...
      </div>
    );
  }

  if (!suggestionsQuery.data || suggestionsQuery.data.items.length === 0) {
    return (
      <div className="rounded-[24px] border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500 shadow-sm">
        אין הצעות עדיין עבור החיפוש הזה.
      </div>
    );
  }

  return (
    <div className="rounded-[24px] border border-slate-200 bg-white shadow-lg shadow-slate-200/70">
      <ul className="divide-y divide-slate-100">
        {suggestionsQuery.data.items.map((item) => (
          <li key={item.id}>
            <button
              type="button"
              onClick={() => onSelect(item.id)}
              className="flex w-full items-center justify-between gap-4 px-4 py-3 text-right transition hover:bg-slate-50"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-slate-900">{item.name}</p>
                <p className="truncate text-xs text-slate-500">
                  {item.brand ? `${item.brand} · ` : ''}
                  {item.unit_description ?? 'פריט להשוואה'}
                </p>
              </div>
              <div className="shrink-0 text-left">
                <p className="text-sm font-bold text-sky-700">{formatCurrency(item.cheapest_price)}</p>
                <p className="text-xs text-slate-500">{item.cheapest_chain_label}</p>
              </div>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
