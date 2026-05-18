import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api';
import AppShell from '../components/AppShell';
import { renderWithQueryClient } from './render';

vi.mock('../app/AuthProvider', () => ({
  useAuth: () => ({
    user: {
      id: 1,
      username: 'demo_user',
      created_at: '2026-05-18T00:00:00Z',
      location_prompt_dismissed: true,
    },
    logout: vi.fn(),
    refresh: vi.fn(),
  }),
}));

vi.mock('../app/theme', () => ({
  useTheme: () => ({ theme: 'light', toggleTheme: vi.fn() }),
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

const status = {
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
};

function LocationDisplay() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname + location.search}</div>;
}

function renderShell(initialPath = '/') {
  return renderWithQueryClient(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<LocationDisplay />} />
          <Route path="/lists" element={<LocationDisplay />} />
          <Route path="/account" element={<LocationDisplay />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe('AppShell', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('submits global search from any authenticated page', async () => {
    vi.spyOn(api, 'getCatalogStatus').mockResolvedValue(status);
    vi.spyOn(api, 'getChains').mockResolvedValue(chains);
    vi.spyOn(api, 'getSuggestions').mockResolvedValue({ query: 'חלב', total: 0, items: [] });

    renderShell('/lists');

    await userEvent.type(screen.getByPlaceholderText('חיפוש מוצר להשוואת מחירים'), 'חלב');
    await userEvent.click(screen.getByRole('button', { name: 'חיפוש' }));

    expect(screen.getByTestId('location')).toHaveTextContent('/?q=%D7%97%D7%9C%D7%91');
  });

  it('removes the old bottom search/lists/settings link bar', async () => {
    vi.spyOn(api, 'getCatalogStatus').mockResolvedValue(status);
    vi.spyOn(api, 'getChains').mockResolvedValue(chains);

    renderShell('/');

    await screen.findByText('Supermarket Compass');
    expect(screen.queryByRole('link', { name: 'חיפוש' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'רשימות' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'הגדרות' })).toBeInTheDocument();
  });

  it('opens a real-time refresh progress popup from refresh click', async () => {
    vi.spyOn(api, 'getCatalogStatus').mockResolvedValue({
      ...status,
      refresh_in_progress: true,
      active_refresh: {
        run_id: 12,
        source: 'manual',
        refresh_kind: 'prices',
        status: 'running',
        started_at: '2026-05-18T00:00:00Z',
        completed_chains: 3,
        total_chains: 10,
        progress_percent: 30,
        current_status_label: 'נסרקו 3 מתוך 10 רשתות',
        chains_scraped: ['carrefour', 'ramilevi'],
        chains_failed: ['quik'],
        products_upserted: 123,
        errors: [],
      },
    });
    vi.spyOn(api, 'getChains').mockResolvedValue(chains);
    vi.spyOn(api, 'triggerCatalogRefresh').mockResolvedValue({
      accepted: false,
      status: 'running',
      detail: 'A catalog refresh is already in progress.',
    });

    renderShell('/');

    await userEvent.click(await screen.findByRole('button', { name: 'הצג התקדמות' }));

    expect(screen.getByRole('dialog', { name: 'התקדמות סריקה' })).toBeInTheDocument();
    expect(screen.getByText('30%')).toBeInTheDocument();
    expect(screen.getByText('123')).toBeInTheDocument();
  });
});
