// API types matching the backend Pydantic models

export interface Deal {
  has_deal: boolean;
  deal_type?: string;
  deal_description?: string;
  deal_price?: number;
  deal_min_qty?: number;
  deal_price_per_unit?: number;
  price_per_base_unit?: number;
  price_per_base_unit_deal?: number;
}

export interface Product {
  id: number;
  chain: string;
  store_id: string;
  store_name: string;
  product_id: string;
  name: string;
  barcode?: string;
  price: number;
  regular_price: number;
  sale_price?: number;
  discount_percent?: number;
  is_weighable: boolean;
  unit_description?: string;
  unit_of_measure?: string;
  unit_qty?: number;
  unit_qty_si?: number;
  unit_dimension?: string;
  price_per_base_unit?: number;
  image_url?: string;
  brand?: string;
  manufacturer?: string;
  deal?: Deal;
  scraped_at: string;
}

export interface SearchResult {
  query: string;
  total: number;
  products: Product[];
}

export interface CartItemInput {
  product_id: number;
}

export interface CartItemOut {
  ref_product_id: number;
  ref_name: string;
  matched_name?: string;
  price?: number;
  barcode?: string;
  image_url?: string;
  found: boolean;
}

export interface StoreCartResult {
  chain: string;
  store_id: string;
  store_name: string;
  total_price: number;
  items: CartItemOut[];
  missing_products: string[];
  has_missing: boolean;
}

export interface CartCompareResult {
  cart_items: string[];
  stores: StoreCartResult[];
}

export interface ScrapeStatus {
  scheduler_running: boolean;
  interval_hours: number;
  last_run?: Record<string, unknown>;
}

export interface ChainInfo {
  chain: string;
  product_count: number;
}
