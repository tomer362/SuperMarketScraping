import axios from 'axios';
import type {
  CartCompareResult,
  CartItemInput,
  ChainInfo,
  Product,
  ScrapeStatus,
  SearchResult,
} from './types';

const api = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
});

export async function searchProducts(
  q: string,
  limit = 50,
  offset = 0,
  chain?: string
): Promise<SearchResult> {
  const params: Record<string, string | number> = { q, limit, offset };
  if (chain) params.chain = chain;
  const res = await api.get<SearchResult>('/search', { params });
  return res.data;
}

export async function getProduct(id: number): Promise<Product> {
  const res = await api.get<Product>(`/product/${id}`);
  return res.data;
}

export async function compareCart(items: CartItemInput[]): Promise<CartCompareResult> {
  const res = await api.post<CartCompareResult>('/cart/compare', items);
  return res.data;
}

export async function getChains(): Promise<ChainInfo[]> {
  const res = await api.get<{ chains: ChainInfo[] }>('/chains');
  return res.data.chains;
}

export async function getScrapeStatus(): Promise<ScrapeStatus> {
  const res = await api.get<ScrapeStatus>('/scrape/status');
  return res.data;
}

export async function triggerScrape(): Promise<unknown> {
  const res = await api.post('/scrape/trigger');
  return res.data;
}
