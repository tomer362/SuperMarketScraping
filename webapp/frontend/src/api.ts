import axios from 'axios';
import type {
  AuthPayload,
  CatalogStatus,
  ChainInfo,
  GenericProductGroupDetail,
  MessageResponse,
  ProductDetail,
  ProductSearchResult,
  RefreshTriggerResult,
  ShoppingListComparison,
  ShoppingListDetail,
  ShoppingListSummary,
  SuggestResult,
} from './types';

const api = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
  withCredentials: true,
});

const apiDebug = import.meta.env.VITE_API_DEBUG === '1' || import.meta.env.VITE_API_DEBUG === 'true';

if (apiDebug) {
  api.interceptors.request.use((config) => {
    const startedAt = performance.now();
    config.headers.set('X-Debug-Client', 'webapp');
    (config as typeof config & { metadata?: { startedAt: number } }).metadata = { startedAt };
    console.debug('[api] request', {
      method: config.method,
      url: config.url,
      params: config.params,
      data: config.data,
    });
    return config;
  });

  api.interceptors.response.use(
    (response) => {
      const metadata = (response.config as typeof response.config & { metadata?: { startedAt: number } }).metadata;
      console.debug('[api] response', {
        method: response.config.method,
        url: response.config.url,
        status: response.status,
        durationMs: metadata ? Math.round(performance.now() - metadata.startedAt) : undefined,
        data: response.data,
      });
      return response;
    },
    (error) => {
      const config = error.config ?? {};
      const metadata = (config as typeof config & { metadata?: { startedAt: number } }).metadata;
      console.debug('[api] error', {
        method: config.method,
        url: config.url,
        status: error.response?.status,
        durationMs: metadata ? Math.round(performance.now() - metadata.startedAt) : undefined,
        data: error.response?.data,
      });
      return Promise.reject(error);
    },
  );
}

export function isApiError(error: unknown): error is { response?: { data?: { detail?: string } } } {
  return typeof error === 'object' && error !== null;
}

export function getApiErrorMessage(error: unknown, fallback: string): string {
  if (isApiError(error)) {
    return error.response?.data?.detail ?? fallback;
  }
  return fallback;
}

export async function register(username: string, password: string): Promise<AuthPayload> {
  const response = await api.post<AuthPayload>('/auth/register', { username, password });
  return response.data;
}

export async function login(username: string, password: string): Promise<AuthPayload> {
  const response = await api.post<AuthPayload>('/auth/login', { username, password });
  return response.data;
}

export async function logout(): Promise<MessageResponse> {
  const response = await api.post<MessageResponse>('/auth/logout');
  return response.data;
}

export async function getCurrentUser(): Promise<AuthPayload> {
  const response = await api.get<AuthPayload>('/auth/me');
  return response.data;
}

export async function getChains(): Promise<ChainInfo[]> {
  const response = await api.get<ChainInfo[]>('/chains');
  return response.data;
}

export async function getSuggestions(
  query: string,
  limit = 8,
  chains?: string[],
): Promise<SuggestResult> {
  const response = await api.get<SuggestResult>('/search/suggest', {
    params: { q: query, limit, chains: chains && chains.length > 0 ? chains.join(',') : undefined },
  });
  return response.data;
}

export async function searchProducts(
  query: string,
  limit = 20,
  offset = 0,
  chains?: string[],
): Promise<ProductSearchResult> {
  const response = await api.get<ProductSearchResult>('/products/search', {
    params: { q: query, limit, offset, chains: chains && chains.length > 0 ? chains.join(',') : undefined },
  });
  return response.data;
}

export async function getProductDetail(productId: number): Promise<ProductDetail> {
  const response = await api.get<ProductDetail>(`/products/${productId}`);
  return response.data;
}

export async function getGenericGroupDetail(groupKey: string): Promise<GenericProductGroupDetail> {
  const response = await api.get<GenericProductGroupDetail>(`/generic-groups/${encodeURIComponent(groupKey)}`);
  return response.data;
}

export async function getLists(): Promise<ShoppingListSummary[]> {
  const response = await api.get<ShoppingListSummary[]>('/lists');
  return response.data;
}

export async function createList(name: string): Promise<ShoppingListDetail> {
  const response = await api.post<ShoppingListDetail>('/lists', { name });
  return response.data;
}

export async function getList(listId: number): Promise<ShoppingListDetail> {
  const response = await api.get<ShoppingListDetail>(`/lists/${listId}`);
  return response.data;
}

export async function renameList(listId: number, name: string): Promise<ShoppingListDetail> {
  const response = await api.patch<ShoppingListDetail>(`/lists/${listId}`, { name });
  return response.data;
}

export async function deleteList(listId: number): Promise<MessageResponse> {
  const response = await api.delete<MessageResponse>(`/lists/${listId}`);
  return response.data;
}

export async function addListItem(
  listId: number,
  canonicalProductId: number,
  quantity = 1,
): Promise<ShoppingListDetail> {
  const response = await api.post<ShoppingListDetail>(`/lists/${listId}/items`, {
    canonical_product_id: canonicalProductId,
    quantity,
  });
  return response.data;
}

export async function addGenericGroupItem(
  listId: number,
  genericGroupKey: string,
  quantity = 1,
): Promise<ShoppingListDetail> {
  const response = await api.post<ShoppingListDetail>(`/lists/${listId}/items`, {
    generic_group_key: genericGroupKey,
    quantity,
  });
  return response.data;
}

export async function updateListItem(
  listId: number,
  itemId: number,
  quantity: number,
): Promise<ShoppingListDetail> {
  const response = await api.patch<ShoppingListDetail>(`/lists/${listId}/items/${itemId}`, {
    quantity,
  });
  return response.data;
}

export async function deleteListItem(listId: number, itemId: number): Promise<ShoppingListDetail> {
  const response = await api.delete<ShoppingListDetail>(`/lists/${listId}/items/${itemId}`);
  return response.data;
}

export async function compareList(listId: number): Promise<ShoppingListComparison> {
  const response = await api.get<ShoppingListComparison>(`/lists/${listId}/comparison`);
  return response.data;
}

export async function getCatalogStatus(): Promise<CatalogStatus> {
  const response = await api.get<CatalogStatus>('/catalog/status');
  return response.data;
}

export async function triggerCatalogRefresh(): Promise<RefreshTriggerResult> {
  const response = await api.post<RefreshTriggerResult>('/catalog/refresh');
  return response.data;
}

export async function triggerCatalogPriceRefresh(): Promise<RefreshTriggerResult> {
  const response = await api.post<RefreshTriggerResult>('/catalog/refresh/prices');
  return response.data;
}

export async function triggerCatalogDealsRefresh(): Promise<RefreshTriggerResult> {
  const response = await api.post<RefreshTriggerResult>('/catalog/refresh/deals');
  return response.data;
}
