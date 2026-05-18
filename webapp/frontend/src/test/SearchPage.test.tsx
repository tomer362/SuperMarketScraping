import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api';
import SearchPage from '../pages/SearchPage';
import { renderWithQueryClient } from './render';

const chain = {
  chain: 'carrefour',
  label: 'קרפור',
  enabled: true,
  status: 'active',
  unavailable_reason: null,
  accent: 'blue',
  product_count: 10,
};

const milkProduct = {
  id: 1,
  name: 'חלב תנובה 3% 1 ליטר',
  brand: 'תנובה',
  manufacturer: 'תנובה',
  barcode: '7290000066882',
  image_url: null,
  unit_description: '1 ליטר',
  unit_of_measure: 'מ"ל',
  unit_qty: 1000,
  unit_qty_si: 1000,
  unit_dimension: 'volume',
  is_weighable: false,
  cheapest_price: 5.5,
  cheapest_chain: 'carrefour',
  cheapest_chain_label: 'קרפור',
  cheapest_store_name: 'קרפור תל אביב',
  chain_count: 3,
  has_deal: true,
};

function LocationDisplay() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

describe('SearchPage', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('does not render instructional filler when no query exists', async () => {
    vi.spyOn(api, 'getChains').mockResolvedValue([chain]);

    renderWithQueryClient(
      <MemoryRouter>
        <SearchPage />
      </MemoryRouter>,
    );

    await waitFor(() => expect(api.getChains).toHaveBeenCalled());
    expect(screen.queryByText('חפשו מוצר מהשורה העליונה')).not.toBeInTheDocument();
    expect(screen.queryByText('Smart search')).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText('למשל: חלב, ביצים, קוטג׳')).not.toBeInTheDocument();
  });

  it('shows the 3-character guard from URL search text', async () => {
    vi.spyOn(api, 'getChains').mockResolvedValue([chain]);

    renderWithQueryClient(
      <MemoryRouter initialEntries={['/?q=חל']}>
        <SearchPage />
      </MemoryRouter>,
    );

    expect(screen.getByText('צריך לפחות 3 תווים לפני שנשלח חיפוש לשרת.')).toBeInTheDocument();
  });

  it('renders search results from URL params', async () => {
    vi.spyOn(api, 'getChains').mockResolvedValue([chain]);
    vi.spyOn(api, 'searchProducts').mockResolvedValue({
      query: 'חלב',
      total: 1,
      products: [milkProduct],
      generic_groups: [],
    });

    renderWithQueryClient(
      <MemoryRouter initialEntries={['/?q=חלב']}>
        <SearchPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('חלב תנובה 3% 1 ליטר')).toBeInTheDocument();
    await waitFor(() =>
      expect(api.searchProducts).toHaveBeenCalledWith('חלב', 20, 0, ['carrefour']),
    );
  });

  it('navigates comparable groups to a group comparison page', async () => {
    vi.spyOn(api, 'getChains').mockResolvedValue([chain]);
    vi.spyOn(api, 'searchProducts').mockResolvedValue({
      query: 'חלב',
      total: 0,
      products: [],
      generic_groups: [
        {
          key: 'milk|3pct|1000ml',
          label: 'חלב 3% 1 ליטר',
          family: 'milk',
          offer_count: 4,
          chain_count: 2,
          cheapest_price: 5.5,
        },
      ],
    });

    renderWithQueryClient(
      <MemoryRouter initialEntries={['/?q=חלב']}>
        <SearchPage />
        <LocationDisplay />
      </MemoryRouter>,
    );

    await userEvent.click(await screen.findByRole('button', { name: /חלב 3% 1 ליטר/ }));

    expect(screen.getByTestId('location')).toHaveTextContent('/groups/milk%7C3pct%7C1000ml');
  });
});
