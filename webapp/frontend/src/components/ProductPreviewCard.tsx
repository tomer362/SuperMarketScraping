import { Link } from 'react-router-dom';
import type { ProductPreview } from '../types';
import { formatCurrency } from '../lib/format';

export default function ProductPreviewCard({ product }: { product: ProductPreview }) {
  return (
    <Link
      to={`/products/${product.id}`}
      className="group flex min-h-32 gap-4 rounded-[28px] border border-white/80 bg-white/90 p-4 shadow-[0_18px_45px_-28px_rgba(15,23,42,0.25)] transition hover:-translate-y-0.5 hover:shadow-[0_26px_60px_-28px_rgba(14,165,233,0.35)]"
    >
      <div className="flex h-20 w-20 shrink-0 items-center justify-center overflow-hidden rounded-[22px] bg-slate-100">
        {product.image_url ? (
          <img src={product.image_url} alt={product.name} className="h-full w-full object-contain" />
        ) : (
          <span className="text-3xl">🛒</span>
        )}
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="line-clamp-2 text-base font-black text-slate-900 sm:text-lg">
              {product.name}
            </h3>
            <p className="mt-1 text-sm text-slate-500">
              {product.brand ? `${product.brand} · ` : ''}
              {product.unit_description ?? 'מוצר להשוואה'}
            </p>
          </div>
          {product.has_deal && (
            <span className="rounded-full bg-emerald-100 px-3 py-1 text-xs font-bold text-emerald-700">
              יש מבצע
            </span>
          )}
        </div>

        <div className="mt-4 flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-400">
              המחיר הנמוך כרגע
            </p>
            <p className="text-2xl font-black text-sky-700">{formatCurrency(product.cheapest_price)}</p>
          </div>
          <div className="text-left text-sm text-slate-500">
            <p>{product.cheapest_chain_label}</p>
            <p>{product.cheapest_store_name}</p>
            <p>{product.chain_count} רשתות זמינות</p>
          </div>
        </div>
      </div>
    </Link>
  );
}
