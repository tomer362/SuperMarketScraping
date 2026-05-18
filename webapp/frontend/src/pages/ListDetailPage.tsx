import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { compareList, deleteList, deleteListItem, getList, renameList, updateListItem } from '../api';
import { formatCurrency, formatQuantity } from '../lib/format';
import { displayableImageUrl } from '../lib/images';
import type { ShoppingListItem } from '../types';

function quantityStep(isWeighable: boolean): number {
  return isWeighable ? 0.1 : 1;
}

function quantityMin(isWeighable: boolean): number {
  return isWeighable ? 0.1 : 1;
}

function normalizeQuantity(value: number, step: number, min: number): number {
  const rounded = Math.round(value / step) * step;
  const clamped = Math.max(min, rounded);
  return Number(clamped.toFixed(step < 1 ? 2 : 0));
}

function quantityUnitLabel(isWeighable: boolean, dimension?: string | null): string {
  if (!isWeighable) {
    return 'יח׳';
  }
  return dimension === 'volume' ? 'ל׳' : 'ק״ג';
}

function listItemName(item: ShoppingListItem): string {
  return item.product?.name ?? item.generic_group?.label ?? 'מוצר להשוואה';
}

export default function ListDetailPage() {
  const { listId } = useParams();
  const navigate = useNavigate();
  const numericListId = Number(listId);
  const queryClient = useQueryClient();
  const [draftName, setDraftName] = useState('');
  const [comparisonOpen, setComparisonOpen] = useState(false);

  const listQuery = useQuery({
    queryKey: ['list', numericListId],
    queryFn: () => getList(numericListId),
    enabled: Number.isFinite(numericListId),
  });

  const comparisonQuery = useQuery({
    queryKey: ['list-comparison', numericListId],
    queryFn: () => compareList(numericListId),
    enabled: comparisonOpen,
  });

  const renameMutation = useMutation({
    mutationFn: (name: string) => renameList(numericListId, name),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['list', numericListId] });
      await queryClient.invalidateQueries({ queryKey: ['lists'] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteList(numericListId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['lists'] });
      navigate('/lists');
    },
  });

  const quantityMutation = useMutation({
    mutationFn: async ({ itemId, quantity }: { itemId: number; quantity: number }) =>
      updateListItem(numericListId, itemId, quantity),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['list', numericListId] }),
        queryClient.invalidateQueries({ queryKey: ['lists'] }),
        queryClient.invalidateQueries({ queryKey: ['list-comparison', numericListId] }),
      ]);
    },
  });

  const deleteItemMutation = useMutation({
    mutationFn: (itemId: number) => deleteListItem(numericListId, itemId),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['list', numericListId] }),
        queryClient.invalidateQueries({ queryKey: ['lists'] }),
        queryClient.invalidateQueries({ queryKey: ['list-comparison', numericListId] }),
      ]);
    },
  });

  if (listQuery.isLoading || !listQuery.data) {
    return (
      <div className="rounded-[34px] border border-dashed border-slate-200 bg-white/95 px-5 py-16 text-center text-slate-500 shadow-sm">
        טוען רשימה...
      </div>
    );
  }

  const shoppingList = listQuery.data;

  return (
    <div className="space-y-5">
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <section className="space-y-4">
          <div className="rounded-[34px] border border-white/80 bg-white/95 p-5 shadow-[0_20px_60px_-36px_rgba(15,23,42,0.35)] sm:p-6">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.24em] text-sky-600">Editable basket</p>
                <h2 className="mt-2 text-2xl font-black text-slate-900">{shoppingList.name}</h2>
                <p className="mt-2 text-sm text-slate-500">
                  {shoppingList.item_count} מוצרים · סה״כ כמות {formatQuantity(shoppingList.total_quantity)}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setComparisonOpen((value) => !value)}
                className="min-h-12 rounded-full bg-slate-900 px-5 text-sm font-black text-white"
              >
                {comparisonOpen ? 'הסתר השוואה' : 'השווה סל'}
              </button>
            </div>

            <div className="mt-5 grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto_auto]">
              <input
                value={draftName || shoppingList.name}
                onChange={(event) => setDraftName(event.target.value)}
                className="min-h-13 rounded-[22px] border border-slate-200 bg-slate-50 px-4 text-base outline-none focus:border-sky-300 focus:bg-white"
              />
              <button
                type="button"
                onClick={() => renameMutation.mutate((draftName || shoppingList.name).trim())}
                className="min-h-13 rounded-[22px] border border-slate-200 px-4 text-sm font-bold text-slate-700"
              >
                שנה שם
              </button>
              <button
                type="button"
                onClick={() => deleteMutation.mutate()}
                className="min-h-13 rounded-[22px] border border-rose-200 bg-rose-50 px-4 text-sm font-bold text-rose-700"
              >
                מחק רשימה
              </button>
            </div>
          </div>

          <div className="space-y-3">
            {shoppingList.items.length === 0 ? (
              <div className="rounded-[30px] border border-dashed border-slate-200 bg-white/95 px-5 py-12 text-center text-slate-500 shadow-sm">
                הרשימה ריקה. <Link to="/" className="font-bold text-sky-700">חזור/י לחיפוש</Link> כדי להוסיף מוצרים.
              </div>
            ) : (
              shoppingList.items.map((item) => (
                <article key={item.id} className="rounded-[30px] border border-white/80 bg-white/95 p-4 shadow-sm">
                  {(() => {
                    const isWeighable = item.product?.is_weighable ?? false;
                    const step = quantityStep(isWeighable);
                    const min = quantityMin(isWeighable);
                    const unitLabel = quantityUnitLabel(isWeighable, item.product?.unit_dimension);
                    const name = listItemName(item);
                    const imageUrl = displayableImageUrl(item.product?.image_url);

                    return (
                  <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                    <div className="flex min-w-0 gap-4">
                      {imageUrl && (
                        <div className="flex h-18 w-18 shrink-0 items-center justify-center overflow-hidden rounded-[22px] bg-slate-100">
                          <img src={imageUrl} alt={name} className="h-full w-full object-contain" />
                        </div>
                      )}
                      <div className="min-w-0">
                        <p className="line-clamp-2 text-base font-black text-slate-900">{name}</p>
                        <p className="mt-1 text-sm text-slate-500">
                          {item.product
                            ? `${item.product.brand ? `${item.product.brand} · ` : ''}${item.product.unit_description ?? 'מוצר להשוואה'}`
                            : `${item.generic_group?.chain_count ?? 0} רשתות · ${item.generic_group?.offer_count ?? 0} הצעות תואמות`}
                        </p>
                        {(item.product?.cheapest_price != null || item.generic_group?.cheapest_price != null) && (
                          <p className="mt-2 text-sm font-bold text-sky-700">
                            החל מ-{formatCurrency(item.product?.cheapest_price ?? item.generic_group?.cheapest_price ?? null)}
                          </p>
                        )}
                      </div>
                    </div>

                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() =>
                          quantityMutation.mutate({
                            itemId: item.id,
                            quantity: normalizeQuantity(item.quantity - step, step, min),
                          })
                        }
                        className="h-11 w-11 rounded-full border border-slate-200 bg-white text-lg font-black text-slate-700"
                        aria-label={`הפחת כמות עבור ${name}`}
                      >
                        -
                      </button>
                      <label className="sr-only" htmlFor={`quantity-${item.id}`}>
                        כמות עבור {name}
                      </label>
                      <input
                        id={`quantity-${item.id}`}
                        type="number"
                        min={min}
                        max={999}
                        step={step}
                        value={item.quantity}
                        onChange={(event) => {
                          const parsed = Number(event.target.value);
                          if (Number.isFinite(parsed) && parsed >= min) {
                            quantityMutation.mutate({
                              itemId: item.id,
                              quantity: normalizeQuantity(parsed, step, min),
                            });
                          }
                        }}
                        className="min-h-11 w-16 rounded-full bg-slate-100 px-3 py-2 text-center text-sm font-black text-slate-900 outline-none"
                      />
                      <span className="text-xs font-semibold text-slate-500">{unitLabel}</span>
                      <button
                        type="button"
                        onClick={() =>
                          quantityMutation.mutate({
                            itemId: item.id,
                            quantity: normalizeQuantity(item.quantity + step, step, min),
                          })
                        }
                        className="h-11 w-11 rounded-full border border-slate-200 bg-white text-lg font-black text-slate-700"
                        aria-label={`הגדל כמות עבור ${name}`}
                      >
                        +
                      </button>
                      <button
                        type="button"
                        onClick={() => deleteItemMutation.mutate(item.id)}
                        className="rounded-full border border-rose-200 bg-rose-50 px-4 py-2 text-sm font-bold text-rose-700"
                      >
                        הסר
                      </button>
                    </div>
                  </div>
                    );
                  })()}
                </article>
              ))
            )}
          </div>
        </section>

        <aside className="space-y-4">
          <div className="rounded-[34px] border border-white/80 bg-white/95 p-5 shadow-sm">
            <p className="text-sm font-black text-slate-900">טיפ להשוואה</p>
            <p className="mt-2 text-sm leading-6 text-slate-500">
              ההשוואה בוחרת את הסניף הזול ביותר בתוך כל רשת עבור כל הסל כולו, ולא מערבבת מחירים מסניפים שונים.
            </p>
          </div>
        </aside>
      </div>

      {comparisonOpen && comparisonQuery.data && (
        <section className="space-y-4 rounded-[34px] border border-slate-200 bg-white/95 p-5 shadow-sm sm:p-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-sky-600">Basket comparison</p>
              <h3 className="mt-2 text-2xl font-black text-slate-900">השוואת סל מלאה</h3>
            </div>
            <p className="rounded-full bg-slate-100 px-4 py-2 text-sm font-semibold text-slate-600">
              כמות כוללת: {formatQuantity(comparisonQuery.data.total_quantity)}
            </p>
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            {comparisonQuery.data.chains.map((chain, index) => (
              <article
                key={`${chain.chain}-${chain.store_id}`}
                className={`rounded-[30px] border p-5 ${
                  index === 0 && chain.complete
                    ? 'border-emerald-200 bg-emerald-50/70'
                    : 'border-slate-200 bg-slate-50/70'
                }`}
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <h4 className="text-lg font-black text-slate-900">{chain.chain_label}</h4>
                      {index === 0 && chain.complete && (
                        <span className="rounded-full bg-emerald-600 px-3 py-1 text-xs font-bold text-white">
                          הכי זול
                        </span>
                      )}
                      {!chain.complete && (
                        <span className="rounded-full bg-amber-100 px-3 py-1 text-xs font-bold text-amber-700">
                          חסרים {chain.missing_count}
                        </span>
                      )}
                    </div>
                    <p className="mt-1 text-sm text-slate-500">{chain.store_name}</p>
                  </div>
                  <div className="text-left">
                    <p className="text-3xl font-black text-slate-900">{formatCurrency(chain.total_price)}</p>
                    {chain.regular_total_price > chain.total_price && (
                      <p className="text-sm text-slate-400 line-through">
                        {formatCurrency(chain.regular_total_price)}
                      </p>
                    )}
                  </div>
                </div>

                {chain.missing_products.length > 0 && (
                  <p className="mt-3 rounded-2xl bg-amber-50 px-4 py-3 text-sm font-medium text-amber-700">
                    חסרים: {chain.missing_products.join(', ')}
                  </p>
                )}

                <div className="mt-4 space-y-2">
                  {chain.items.map((item) => (
                    <div key={item.list_item_id} className="rounded-2xl bg-white px-4 py-3 shadow-sm">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-bold text-slate-900">{item.product_name}</p>
                          <p className="mt-1 text-xs text-slate-500">
                            כמות {formatQuantity(item.quantity)}
                            {item.fulfillment_description ? ` · ${item.fulfillment_description}` : ''}
                            {item.deal_applied && item.deal_description ? ` · ${item.deal_description}` : ''}
                          </p>
                          {item.product_url && (
                            <a
                              href={item.product_url}
                              target="_blank"
                              rel="noreferrer"
                              className="mt-2 inline-flex text-xs font-bold text-sky-700 transition hover:text-sky-900"
                            >
                              לעמוד המוצר
                            </a>
                          )}
                        </div>
                        <p className="text-sm font-black text-slate-900">
                          {item.found ? formatCurrency(item.line_total ?? null) : 'לא זמין'}
                        </p>
                      </div>
                    </div>
                  ))}
                </div>
              </article>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
