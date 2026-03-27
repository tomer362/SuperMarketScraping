import type { Product } from '../types';

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

// Chain brand colors
const CHAIN_COLORS: Record<string, string> = {
  shufersal: 'bg-red-100 text-red-700',
  tivtaam: 'bg-orange-100 text-orange-700',
  carrefour: 'bg-blue-100 text-blue-700',
  machsanei: 'bg-yellow-100 text-yellow-700',
  ramilevi: 'bg-green-100 text-green-700',
  yochananof: 'bg-purple-100 text-purple-700',
  keshet: 'bg-pink-100 text-pink-700',
  quik: 'bg-cyan-100 text-cyan-700',
  victory: 'bg-teal-100 text-teal-700',
  ybitan: 'bg-indigo-100 text-indigo-700',
};

interface ProductCardProps {
  product: Product;
  onAddToCart?: (product: Product) => void;
  inCart?: boolean;
}

export default function ProductCard({ product, onAddToCart, inCart }: ProductCardProps) {
  const chainLabel = CHAIN_NAMES[product.chain] ?? product.chain;
  const chainColor = CHAIN_COLORS[product.chain] ?? 'bg-gray-100 text-gray-700';
  const hasDiscount =
    product.discount_percent != null && product.discount_percent > 0;

  const placeholder =
    'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80"><rect width="80" height="80" fill="%23e2e8f0"/><text x="40" y="44" text-anchor="middle" font-size="30" fill="%2394a3b8">🛒</text></svg>';

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-4 flex gap-3 hover:shadow-md transition-shadow">
      {/* Image */}
      <div className="w-20 h-20 flex-shrink-0 rounded-xl overflow-hidden bg-slate-50 flex items-center justify-center">
        <img
          src={product.image_url ?? placeholder}
          alt={product.name}
          className="w-full h-full object-contain"
          onError={(e) => {
            (e.target as HTMLImageElement).src = placeholder;
          }}
        />
      </div>

      {/* Info */}
      <div className="flex-1 min-w-0">
        <div className="flex items-start justify-between gap-2">
          <h3 className="text-sm font-semibold text-slate-800 leading-snug line-clamp-2">
            {product.name}
          </h3>
          <span
            className={`text-xs px-2 py-0.5 rounded-full font-medium whitespace-nowrap flex-shrink-0 ${chainColor}`}
          >
            {chainLabel}
          </span>
        </div>

        {product.brand && (
          <p className="text-xs text-slate-400 mt-0.5">{product.brand}</p>
        )}

        {product.store_name && (
          <p className="text-xs text-slate-400">{product.store_name}</p>
        )}

        {/* Price row */}
        <div className="mt-2 flex items-center gap-2 flex-wrap">
          <span className="text-lg font-bold text-slate-900">
            ₪{product.price.toFixed(2)}
          </span>
          {hasDiscount && product.regular_price !== product.price && (
            <span className="text-xs text-slate-400 line-through">
              ₪{product.regular_price.toFixed(2)}
            </span>
          )}
          {hasDiscount && (
            <span className="text-xs bg-red-50 text-red-600 px-1.5 py-0.5 rounded-full font-medium">
              -{product.discount_percent!.toFixed(0)}%
            </span>
          )}
        </div>

        {/* Deal badge */}
        {product.deal?.has_deal && product.deal.deal_description && (
          <p className="text-xs text-emerald-600 mt-1 font-medium truncate">
            🏷 {product.deal.deal_description}
          </p>
        )}

        {/* Unit price */}
        {product.price_per_base_unit != null && product.unit_of_measure && (
          <p className="text-xs text-slate-400 mt-0.5">
            ₪{product.price_per_base_unit.toFixed(2)} / 100{product.unit_of_measure}
          </p>
        )}
      </div>

      {/* Add to cart button */}
      {onAddToCart && (
        <div className="flex-shrink-0 flex items-center">
          <button
            onClick={() => onAddToCart(product)}
            disabled={inCart}
            className={`w-9 h-9 rounded-full flex items-center justify-center text-lg transition-colors ${
              inCart
                ? 'bg-emerald-100 text-emerald-600 cursor-default'
                : 'bg-slate-100 hover:bg-blue-100 hover:text-blue-600 text-slate-500 cursor-pointer'
            }`}
            title={inCart ? 'כבר ברשימה' : 'הוסף לרשימה'}
          >
            {inCart ? '✓' : '+'}
          </button>
        </div>
      )}
    </div>
  );
}
