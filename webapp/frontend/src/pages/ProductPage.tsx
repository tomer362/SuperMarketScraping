import { useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getProductDetail } from '../api';
import ListPickerDialog from '../components/ListPickerDialog';
import { formatComparisonUnit, formatCurrency } from '../lib/format';
import { displayableImageUrl } from '../lib/images';

export default function ProductPage() {
  const { productId } = useParams();
  const [searchParams] = useSearchParams();
  const [dialogOpen, setDialogOpen] = useState(false);

  const productQuery = useQuery({
    queryKey: ['product', productId],
    queryFn: () => getProductDetail(Number(productId)),
    enabled: Boolean(productId),
  });

  if (productQuery.isLoading) {
    return <PageState text="טוען פרטי מוצר..." />;
  }

  if (!productQuery.data) {
    return <PageState text="לא מצאנו את המוצר הזה." />;
  }

  const product = productQuery.data;
  const requestedQuantity = Number(searchParams.get('qty') ?? 1);
  const requestedDimension = searchParams.get('dim');
  const defaultQuantity =
    Number.isFinite(requestedQuantity)
    && requestedQuantity > 0
    && (requestedDimension === 'count' || (product.is_weighable && requestedDimension === product.unit_dimension))
      ? requestedQuantity
      : 1;
  const defaultQuantityDimension = defaultQuantity === 1 ? null : requestedDimension;
  const imageUrl = displayableImageUrl(product.image_url);

  return (
    <>
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.1fr)_20rem]">
        <section className="space-y-4">
          <div className="rounded-[34px] border border-white/80 bg-white/95 p-5 shadow-[0_20px_60px_-36px_rgba(15,23,42,0.35)] sm:p-6">
            <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
              <div className="flex gap-4">
                {imageUrl && (
                  <div className="flex h-24 w-24 shrink-0 items-center justify-center overflow-hidden rounded-[26px] bg-slate-100">
                    <img src={imageUrl} alt={product.name} className="h-full w-full object-contain" />
                  </div>
                )}
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.24em] text-sky-600">Exact product</p>
                  <h2 className="mt-2 text-2xl font-black text-slate-900 sm:text-3xl">{product.name}</h2>
                  <p className="mt-2 text-sm text-slate-500">
                    {product.brand ? `${product.brand} · ` : ''}
                    {product.unit_description ?? 'מוצר להשוואת מחירים'}
                  </p>
                  {product.barcode && <p className="mt-1 text-xs text-slate-400">ברקוד: {product.barcode}</p>}
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
                <p className="text-4xl font-black text-sky-300">{formatCurrency(product.cheapest_price)}</p>
                <p className="text-sm text-slate-300">{product.chain_count} רשתות זמינות להשוואה</p>
              </div>
            </div>
          </div>

          <div className="space-y-3">
            {product.offers.map((offer, index) => (
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
                    {offer.product_url && (
                      <a
                        href={offer.product_url}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-3 inline-flex text-sm font-bold text-sky-700 transition hover:text-sky-900"
                      >
                        לעמוד המוצר בסופר
                      </a>
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
            <p className="text-sm font-black text-slate-900">מה אפשר לעשות כאן?</p>
            <ul className="mt-3 space-y-3 text-sm leading-6 text-slate-500">
              <li>בדוק/י באיזו רשת המוצר הזה הכי זול עכשיו.</li>
              <li>הוסף/י אותו לרשימה אחת או ליותר.</li>
              <li>השתמש/י בהשוואת סל כדי למצוא את הסניף הזול ביותר לכל רשת.</li>
            </ul>
          </div>
        </aside>
      </div>

      <ListPickerDialog
        product={product}
        defaultQuantity={defaultQuantity}
        defaultQuantityDimension={defaultQuantityDimension}
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
