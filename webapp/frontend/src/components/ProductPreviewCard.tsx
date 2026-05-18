import { Link } from 'react-router-dom';
import type { ProductPreview } from '../types';
import { formatCurrency } from '../lib/format';
import ProductImage from './ProductImage';

export default function ProductPreviewCard({
  product,
  detailParams = '',
}: {
  product: ProductPreview;
  detailParams?: string;
}) {
  return (
    <Link
      to={`/products/${product.id}${detailParams}`}
      className="group flex min-h-28 gap-3 rounded-[22px] border border-white/80 bg-white/90 p-3 shadow-[0_18px_45px_-30px_rgba(15,23,42,0.25)] transition hover:-translate-y-0.5 hover:shadow-[0_26px_60px_-30px_rgba(14,165,233,0.35)] sm:gap-4 sm:p-4"
    >
      <ProductImage
        imageUrl={product.image_url}
        alt={product.name}
        frameClassName="flex h-16 w-16 shrink-0 items-center justify-center overflow-hidden rounded-[18px] bg-slate-100 sm:h-20 sm:w-20"
      />

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

        <div className="mt-3 flex items-end justify-between gap-3">
          <div>
            <p className="text-[0.68rem] font-semibold uppercase tracking-[0.16em] text-slate-400">
              המחיר הנמוך כרגע
            </p>
            <p className="text-xl font-black text-sky-700 sm:text-2xl">{formatCurrency(product.cheapest_price)}</p>
          </div>
          <div className="shrink-0 text-left text-xs text-slate-500 sm:text-sm">
            <p>{product.cheapest_chain_label}</p>
            <p>{product.cheapest_store_name}</p>
            <p>{product.chain_count} רשתות זמינות</p>
          </div>
        </div>
      </div>
    </Link>
  );
}
