import { screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api';
import AccountPage from '../pages/AccountPage';
import { renderWithQueryClient } from './render';

vi.mock('../app/AuthProvider', () => ({
  useAuth: () => ({
    user: {
      id: 1,
      username: 'demo_user',
      created_at: '2026-05-18T00:00:00Z',
      location_prompt_dismissed: true,
    },
    refresh: vi.fn(),
  }),
}));

const chains = [
  {
    chain: 'carrefour',
    label: 'קרפור',
    enabled: true,
    status: 'active',
    unavailable_reason: null,
    accent: 'blue',
    product_count: 10,
  },
];

describe('AccountPage', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('uses the chain filter as the only chain list', async () => {
    vi.spyOn(api, 'getChains').mockResolvedValue(chains);
    vi.spyOn(api, 'getCatalogStatus').mockResolvedValue({
      scheduler_running: false,
      refresh_in_progress: false,
      active_refresh: null,
      interval_hours: 24,
      price_interval_hours: 24,
      deals_interval_hours: 4,
      catalog_fresh: true,
      prices_fresh: true,
      deals_fresh: true,
      last_refresh: null,
      last_successful_refresh: null,
      last_price_refresh: null,
      last_successful_price_refresh: null,
      last_deals_refresh: null,
      last_successful_deals_refresh: null,
      chains,
    });

    renderWithQueryClient(<AccountPage />);

    expect(await screen.findByText('סינון רשתות לחיפוש')).toBeInTheDocument();
    expect(screen.queryByText('רשתות פעילות')).not.toBeInTheDocument();
  });
});
