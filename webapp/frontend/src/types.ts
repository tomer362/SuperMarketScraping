export interface User {
  id: number;
  username: string;
  created_at: string;
}

export interface AuthPayload {
  user: User;
}

export interface ChainInfo {
  chain: string;
  label: string;
  enabled: boolean;
  status: string;
  unavailable_reason?: string | null;
  accent: string;
  product_count: number;
}

export interface SuggestItem {
  id: number;
  name: string;
  brand?: string | null;
  unit_description?: string | null;
  image_url?: string | null;
  cheapest_price: number;
  cheapest_chain: string;
  cheapest_chain_label: string;
}

export interface SuggestResult {
  query: string;
  total: number;
  items: SuggestItem[];
}

export interface ProductPreview {
  id: number;
  name: string;
  brand?: string | null;
  manufacturer?: string | null;
  barcode?: string | null;
  image_url?: string | null;
  unit_description?: string | null;
  unit_of_measure?: string | null;
  unit_qty?: number | null;
  unit_qty_si?: number | null;
  unit_dimension?: string | null;
  is_weighable: boolean;
  cheapest_price: number;
  cheapest_chain: string;
  cheapest_chain_label: string;
  cheapest_store_name: string;
  chain_count: number;
  has_deal: boolean;
}

export interface GenericProductGroup {
  key: string;
  label: string;
  family: string;
  offer_count: number;
  chain_count: number;
  cheapest_price?: number | null;
}

export interface ProductSearchResult {
  query: string;
  total: number;
  products: ProductPreview[];
  generic_groups?: GenericProductGroup[];
}

export interface Deal {
  has_deal: boolean;
  deal_type?: string | null;
  deal_description?: string | null;
  deal_price?: number | null;
  deal_min_qty?: number | null;
  deal_price_per_unit?: number | null;
  price_per_base_unit?: number | null;
  price_per_base_unit_deal?: number | null;
}

export interface ChainOffer {
  id: number;
  chain: string;
  chain_label: string;
  store_id: string;
  store_name: string;
  product_id: string;
  name: string;
  price: number;
  regular_price: number;
  sale_price?: number | null;
  discount_percent?: number | null;
  price_per_base_unit?: number | null;
  brand?: string | null;
  image_url?: string | null;
  deal?: Deal | null;
  scraped_at: string;
}

export interface ProductDetail {
  id: number;
  name: string;
  brand?: string | null;
  manufacturer?: string | null;
  barcode?: string | null;
  image_url?: string | null;
  unit_description?: string | null;
  unit_of_measure?: string | null;
  unit_qty?: number | null;
  unit_qty_si?: number | null;
  unit_dimension?: string | null;
  is_weighable: boolean;
  cheapest_price: number;
  chain_count: number;
  offers: ChainOffer[];
}

export interface ShoppingListSummary {
  id: number;
  name: string;
  item_count: number;
  total_quantity: number;
  updated_at: string;
}

export interface ShoppingListItem {
  id: number;
  quantity: number;
  product?: ProductPreview | null;
  generic_group?: GenericProductGroup | null;
}

export interface ShoppingListDetail extends ShoppingListSummary {
  items: ShoppingListItem[];
}

export interface BasketComparisonLine {
  list_item_id: number;
  canonical_product_id?: number | null;
  generic_group_key?: string | null;
  product_name: string;
  quantity: number;
  matched_name?: string | null;
  unit_price?: number | null;
  regular_unit_price?: number | null;
  line_total?: number | null;
  regular_line_total?: number | null;
  deal_applied: boolean;
  deal_description?: string | null;
  image_url?: string | null;
  found: boolean;
}

export interface BasketComparisonChain {
  chain: string;
  chain_label: string;
  store_id: string;
  store_name: string;
  total_price: number;
  regular_total_price: number;
  complete: boolean;
  missing_count: number;
  missing_products: string[];
  applied_deals_count: number;
  items: BasketComparisonLine[];
}

export interface ShoppingListComparison {
  list_id: number;
  list_name: string;
  item_count: number;
  total_quantity: number;
  chains: BasketComparisonChain[];
}

export interface RefreshRun {
  run_id: number;
  source: string;
  status: string;
  started_at?: string | null;
  finished_at?: string | null;
  chains_scraped: string[];
  chains_failed: string[];
  products_upserted: number;
  errors: string[];
}

export interface CatalogStatus {
  scheduler_running: boolean;
  refresh_in_progress: boolean;
  interval_hours: number;
  catalog_fresh: boolean;
  last_refresh?: RefreshRun | null;
  last_successful_refresh?: RefreshRun | null;
  chains: ChainInfo[];
}

export interface RefreshTriggerResult {
  accepted: boolean;
  status: string;
  detail: string;
}

export interface MessageResponse {
  detail: string;
}
