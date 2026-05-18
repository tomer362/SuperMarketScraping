import { useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getGenericGroupDetail } from '../api';
import ListPickerDialog from '../components/ListPickerDialog';
import { formatComparisonUnit, formatCurrency } from '../lib/format';

function routeGroupKey(value: string | undefined): string {
  if (!value) {
    return '';
  }
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

export default function GroupPage() {
  const { groupKey: groupKeyParam } = useParams();
  const [searchParams] = useSearchParams();
  const groupKey = routeGroupKey(groupKeyParam);
  const [dialogOpen, setDialogOpen] = useState(false);

  const groupQuery = useQuery({
    queryKey: ['generic-group', groupKey],
    queryFn: () => getGenericGroupDetail(groupKey),
    enabled: groupKey.length > 0,
  });

  if (groupQuery.isLoading) {
    return <PageState text="טוען קבוצת השוואה..." />;
  }

  if (!groupQuery.data) {
    return <PageState text="לא מצאנו את קבוצת ההשוואה הזו." />;
  }

  const group = groupQuery.data;
  const requestedQuantity = Number(searchParams.get('qty') ?? 1);
  const requestedDimension = searchParams.get('dim');
  const defaultQuantity = Number.isFinite(requestedQuantity) && requestedQuantity > 0 ? requestedQuantity : 1;

  return (
    <>
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.1fr)_20rem]">
        <section className="space-y-4">
          <div className="rounded-[34px] border border-white/80 bg-white/95 p-5 shadow-[0_20px_60px_-36px_rgba(15,23,42,0.35)] sm:p-6">
            <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
              <div className="flex gap-4">
                <div className="flex h-24 w-24 shrink-0 items-center justify-center overflow-hidden rounded-[26px] bg-emerald-50">
                  {group.offers[0]?.image_url ? (
                    <img src={group.offers[0].image_url} alt={group.label} className="h-full w-full object-contain" />
                  ) : (
                    <span className="text-4xl">🛒</span>
                  )}
                </div>
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.24em] text-emerald-700">
                    Comparable group
                  </p>
                  <h2 className="mt-2 text-2xl font-black text-slate-900 sm:text-3xl">{group.label}</h2>
                  <p className="mt-2 text-sm text-slate-500">
                    השוואה לפי סוג וגודל זהים, עם מותג גמיש רק בקטגוריות שאושרו לכך.
                  </p>
                </div>
              </div>

              <button
                type="button"
                onClick={() => setDialogOpen(true)}
                className="min-h-12 rounded-full bg-slate-900 px-5 text-sm font-black text-white transition hover:bg-slate-800"
              >
                הוסף לרשימה
              </button>
            </div>

            <div className="mt-5 rounded-[28px] bg-slate-950 px-5 py-4 text-white shadow-lg shadow-slate-900/20">
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-300">המחיר הנמוך כעת</p>
              <div className="mt-2 flex flex-wrap items-end justify-between gap-3">
                <p className="text-4xl font-black text-emerald-300">{formatCurrency(group.cheapest_price ?? null)}</p>
                <p className="text-sm text-slate-300">
                  {group.chain_count} רשתות · {group.offer_count} הצעות תואמות
                </p>
              </div>
            </div>
          </div>

          <div className="space-y-3">
            {group.offers.map((offer, index) => (
              <article
                key={offer.chain}
                className={`rounded-[30px] border px-5 py-4 shadow-sm ${
                  index === 0
                    ? 'border-emerald-200 bg-emerald-50/70 shadow-emerald-100'
                    : 'border-white/80 bg-white/95'
                }`}
              >
                <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-lg font-black text-slate-900">{offer.chain_label}</h3>
                      {index === 0 && (
                        <span className="rounded-full bg-emerald-600 px-3 py-1 text-xs font-bold text-white">
                          הכי זול
                        </span>
                      )}
                      {offer.deal?.has_deal && (
                        <span className="rounded-full bg-sky-100 px-3 py-1 text-xs font-bold text-sky-700">
                          יש מבצע
                        </span>
                      )}
                    </div>
                    <p className="mt-1 text-sm text-slate-500">{offer.store_name}</p>
                    <p className="mt-1 text-sm text-slate-500">{offer.name}</p>
                    {offer.deal?.deal_description && (
                      <p className="mt-2 text-sm font-semibold text-emerald-700">{offer.deal.deal_description}</p>
                    )}
                  </div>

                  <div className="text-left">
                    <p className="text-3xl font-black text-slate-900">{formatCurrency(offer.price)}</p>
                    {offer.regular_price > offer.price && (
                      <p className="mt-1 text-sm text-slate-400 line-through">
                        {formatCurrency(offer.regular_price)}
                      </p>
                    )}
                    {offer.price_per_base_unit != null && formatComparisonUnit(offer) && (
                      <p className="mt-2 text-xs font-semibold text-slate-500">
                        {formatCurrency(offer.price_per_base_unit)} / {formatComparisonUnit(offer)}
                      </p>
                    )}
                  </div>
                </div>
              </article>
            ))}
          </div>
        </section>

        <aside className="space-y-4">
          <div className="rounded-[34px] border border-white/80 bg-white/95 p-5 shadow-sm">
            <p className="text-sm font-black text-slate-900">מה מושווה כאן?</p>
            <ul className="mt-3 space-y-3 text-sm leading-6 text-slate-500">
              <li>כל שורה היא ההצעה הזולה ביותר באותה רשת עבור הקבוצה הזו.</li>
              <li>שם המוצר שמופיע בכל רשת הוא המוצר שהמערכת התאימה בפועל.</li>
              <li>הוספה לרשימה תאפשר להשוות סל מלא עם אותו מוצר כללי.</li>
            </ul>
          </div>
        </aside>
      </div>

      <ListPickerDialog
        group={group}
        defaultQuantity={defaultQuantity}
        defaultQuantityDimension={requestedDimension}
        isOpen={dialogOpen}
        onClose={() => setDialogOpen(false)}
      />
    </>
  );
}

function PageState({ text }: { text: string }) {
  return (
    <div className="rounded-[34px] border border-dashed border-slate-200 bg-white/95 px-5 py-16 text-center text-slate-500 shadow-sm">
      {text}
    </div>
  );
}
