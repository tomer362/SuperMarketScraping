import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api';
import AppShell from '../components/AppShell';
import { renderWithQueryClient } from './render';

const authMock = vi.hoisted(() => ({
  user: {
    id: 1,
    username: 'demo_user',
    created_at: '2026-05-18T00:00:00Z',
    location_prompt_dismissed: true,
    location_lat: null,
    location_lng: null,
  },
  logout: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock('../app/AuthProvider', () => ({
  useAuth: () => ({
    user: authMock.user,
    logout: authMock.logout,
    refresh: authMock.refresh,
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
    authMock.user = {
      id: 1,
      username: 'demo_user',
      created_at: '2026-05-18T00:00:00Z',
      location_prompt_dismissed: true,
      location_lat: null,
      location_lng: null,
    };
    authMock.logout.mockClear();
    authMock.refresh.mockClear();
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

  it('asks for confirmation before starting a new catalog refresh', async () => {
    vi.spyOn(api, 'getCatalogStatus').mockResolvedValue(status);
    vi.spyOn(api, 'getChains').mockResolvedValue(chains);
    const refreshSpy = vi.spyOn(api, 'triggerCatalogRefresh').mockResolvedValue({
      accepted: true,
      status: 'started',
      detail: 'Catalog prices refresh started.',
    });

    renderShell('/');

    await userEvent.click(await screen.findByRole('button', { name: 'רענן קטלוג' }));

    expect(screen.getByRole('dialog', { name: 'לרענן את הקטלוג עכשיו?' })).toBeInTheDocument();
    expect(refreshSpy).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', { name: 'כן, רענן' }));

    await waitFor(() => expect(refreshSpy).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('dialog', { name: 'לרענן את הקטלוג עכשיו?' })).not.toBeInTheDocument();
  });

  it('cancels catalog refresh confirmation without starting refresh', async () => {
    vi.spyOn(api, 'getCatalogStatus').mockResolvedValue(status);
    vi.spyOn(api, 'getChains').mockResolvedValue(chains);
    const refreshSpy = vi.spyOn(api, 'triggerCatalogRefresh').mockResolvedValue({
      accepted: true,
      status: 'started',
      detail: 'Catalog prices refresh started.',
    });

    renderShell('/');

    await userEvent.click(await screen.findByRole('button', { name: 'רענן קטלוג' }));
    await userEvent.click(screen.getByRole('button', { name: 'ביטול' }));

    expect(refreshSpy).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog', { name: 'לרענן את הקטלוג עכשיו?' })).not.toBeInTheDocument();
  });

  it('shows a starting state if progress details lag behind refresh status', async () => {
    vi.spyOn(api, 'getCatalogStatus').mockResolvedValue({
      ...status,
      refresh_in_progress: true,
      active_refresh: null,
    });
    vi.spyOn(api, 'getChains').mockResolvedValue(chains);

    renderShell('/');

    await userEvent.click(await screen.findByRole('button', { name: 'הצג התקדמות' }));

    expect(screen.getByRole('dialog', { name: 'התקדמות סריקה' })).toBeInTheDocument();
    expect(screen.getByText('מתחיל רענון קטלוג...')).toBeInTheDocument();
    expect(screen.queryByText('אין רענון פעיל')).not.toBeInTheDocument();
  });

  it('closes the progress popup with Escape', async () => {
    vi.spyOn(api, 'getCatalogStatus').mockResolvedValue({
      ...status,
      refresh_in_progress: true,
      active_refresh: null,
    });
    vi.spyOn(api, 'getChains').mockResolvedValue(chains);

    renderShell('/');

    await userEvent.click(await screen.findByRole('button', { name: 'הצג התקדמות' }));
    expect(screen.getByRole('dialog', { name: 'התקדמות סריקה' })).toBeInTheDocument();

    await userEvent.keyboard('{Escape}');

    expect(screen.queryByRole('dialog', { name: 'התקדמות סריקה' })).not.toBeInTheDocument();
  });

  it('hides the location prompt immediately after using current location', async () => {
    authMock.user = {
      ...authMock.user,
      location_prompt_dismissed: false,
      location_lat: null,
      location_lng: null,
    };
    vi.spyOn(api, 'getCatalogStatus').mockResolvedValue(status);
    vi.spyOn(api, 'getChains').mockResolvedValue(chains);
    vi.spyOn(api, 'saveCurrentLocation').mockResolvedValue({
      user: {
        ...authMock.user,
        location_lat: 32.0853,
        location_lng: 34.7818,
        location_label: 'המיקום הנוכחי',
        location_source: 'gps',
        location_updated_at: '2026-05-18T00:00:00Z',
        location_prompt_dismissed: true,
      },
    });
    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: {
        getCurrentPosition: vi.fn((success: PositionCallback) =>
          success({
            coords: {
              latitude: 32.0853,
              longitude: 34.7818,
              accuracy: 1,
              altitude: null,
              altitudeAccuracy: null,
              heading: null,
              speed: null,
            },
            timestamp: Date.now(),
          } as GeolocationPosition),
        ),
      },
    });

    renderShell('/');

    expect(await screen.findByText('להציג סופרים קרובים יותר?')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'השתמש במיקום' }));

    await waitFor(() =>
      expect(screen.queryByText('להציג סופרים קרובים יותר?')).not.toBeInTheDocument(),
    );
  });
});
