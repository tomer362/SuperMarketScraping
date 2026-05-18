import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api';
import SearchPage from '../pages/SearchPage';
import { renderWithQueryClient } from './render';

function LocationDisplay() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

describe('SearchPage', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows the 3-character guard before searching', async () => {
    vi.spyOn(api, 'getChains').mockResolvedValue([
      {
        chain: 'carrefour',
        label: 'קרפור',
        enabled: true,
        status: 'active',
        unavailable_reason: null,
        accent: 'blue',
        product_count: 10,
      },
    ]);
    vi.spyOn(api, 'getSuggestions').mockResolvedValue({ query: 'חל', total: 0, items: [] });

    renderWithQueryClient(
      <MemoryRouter>
        <SearchPage />
      </MemoryRouter>,
    );

    const input = screen.getByPlaceholderText('למשל: חלב, ביצים, קוטג׳');
    await userEvent.type(input, 'חל');
    expect(screen.getByText('צריך לפחות 3 תווים לפני שנשלח חיפוש לשרת.')).toBeInTheDocument();
  });

  it('renders search results after submit', async () => {
    vi.spyOn(api, 'getChains').mockResolvedValue([
      {
        chain: 'carrefour',
        label: 'קרפור',
        enabled: true,
        status: 'active',
        unavailable_reason: null,
        accent: 'blue',
        product_count: 10,
      },
    ]);
    vi.spyOn(api, 'searchProducts').mockResolvedValue({
      query: 'חלב',
      total: 1,
      products: [
        {
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
        },
      ],
    });
    vi.spyOn(api, 'getSuggestions').mockResolvedValue({ query: 'חלב', total: 0, items: [] });

    renderWithQueryClient(
      <MemoryRouter>
        <SearchPage />
      </MemoryRouter>,
    );

    const input = screen.getByPlaceholderText('למשל: חלב, ביצים, קוטג׳');
    await userEvent.type(input, 'חלב');
    await userEvent.click(screen.getByRole('button', { name: 'חיפוש' }));

    expect(await screen.findByText('חלב תנובה 3% 1 ליטר')).toBeInTheDocument();
    await waitFor(() => expect(api.searchProducts).toHaveBeenCalled());
    await waitFor(() =>
      expect(api.searchProducts).toHaveBeenCalledWith('חלב', 20, 0, ['carrefour']),
    );
  });

  it('navigates comparable groups to a group comparison page', async () => {
    vi.spyOn(api, 'getChains').mockResolvedValue([
      {
        chain: 'carrefour',
        label: 'קרפור',
        enabled: true,
        status: 'active',
        unavailable_reason: null,
        accent: 'blue',
        product_count: 10,
      },
    ]);
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
    vi.spyOn(api, 'getSuggestions').mockResolvedValue({ query: 'חלב', total: 0, items: [] });

    renderWithQueryClient(
      <MemoryRouter>
        <SearchPage />
        <LocationDisplay />
      </MemoryRouter>,
    );

    const input = screen.getByPlaceholderText('למשל: חלב, ביצים, קוטג׳');
    await userEvent.type(input, 'חלב');
    await userEvent.click(screen.getByRole('button', { name: 'חיפוש' }));
    await userEvent.click(await screen.findByRole('button', { name: /חלב 3% 1 ליטר/ }));

    expect(screen.getByTestId('location')).toHaveTextContent('/groups/milk%7C3pct%7C1000ml');
  });

  it('restores submitted search from URL params', async () => {
    vi.spyOn(api, 'getChains').mockResolvedValue([
      {
        chain: 'carrefour',
        label: 'קרפור',
        enabled: true,
        status: 'active',
        unavailable_reason: null,
        accent: 'blue',
        product_count: 10,
      },
    ]);
    vi.spyOn(api, 'searchProducts').mockResolvedValue({
      query: 'חלב',
      total: 1,
      products: [
        {
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
        },
      ],
    });
    vi.spyOn(api, 'getSuggestions').mockResolvedValue({ query: 'חלב', total: 0, items: [] });

    renderWithQueryClient(
      <MemoryRouter initialEntries={['/?q=חלב']}>
        <SearchPage />
      </MemoryRouter>,
    );

    expect(screen.getByPlaceholderText('למשל: חלב, ביצים, קוטג׳')).toHaveValue('חלב');
    await waitFor(() => expect(api.searchProducts).toHaveBeenCalledWith('חלב', 20, 0, ['carrefour']));
  });

  it('does not show the cheapest chain label in autocomplete rows', async () => {
    vi.spyOn(api, 'getChains').mockResolvedValue([
      {
        chain: 'carrefour',
        label: 'קרפור',
        enabled: true,
        status: 'active',
        unavailable_reason: null,
        accent: 'blue',
        product_count: 10,
      },
    ]);
    vi.spyOn(api, 'getSuggestions').mockResolvedValue({
      query: 'חלב',
      total: 1,
      items: [
        {
          id: 1,
          name: 'חלב תנובה 3% 1 ליטר',
          brand: 'תנובה',
          unit_description: '1 ליטר',
          image_url: null,
          cheapest_price: 5.5,
          cheapest_chain: 'carrefour',
          cheapest_chain_label: 'קרפור',
        },
      ],
    });

    renderWithQueryClient(
      <MemoryRouter>
        <SearchPage />
      </MemoryRouter>,
    );

    const input = screen.getByPlaceholderText('למשל: חלב, ביצים, קוטג׳');
    await userEvent.type(input, 'חלב');

    expect(await screen.findByText('מחיר התחלתי')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /חלב תנובה/ })).not.toHaveTextContent('קרפור');
  });

  it('hides autocomplete after submitting with Enter until the query changes', async () => {
    vi.spyOn(api, 'getChains').mockResolvedValue([
      {
        chain: 'carrefour',
        label: 'קרפור',
        enabled: true,
        status: 'active',
        unavailable_reason: null,
        accent: 'blue',
        product_count: 10,
      },
    ]);
    vi.spyOn(api, 'getSuggestions').mockResolvedValue({
      query: 'חלב',
      total: 1,
      items: [
        {
          id: 1,
          name: 'חלב תנובה 3% 1 ליטר',
          brand: 'תנובה',
          unit_description: '1 ליטר',
          image_url: null,
          cheapest_price: 5.5,
          cheapest_chain: 'carrefour',
          cheapest_chain_label: 'קרפור',
        },
      ],
    });
    vi.spyOn(api, 'searchProducts').mockResolvedValue({
      query: 'חלב',
      total: 0,
      products: [],
      generic_groups: [],
    });

    renderWithQueryClient(
      <MemoryRouter>
        <SearchPage />
      </MemoryRouter>,
    );

    const input = screen.getByPlaceholderText('למשל: חלב, ביצים, קוטג׳');
    await userEvent.type(input, 'חלב');
    expect(await screen.findByText('מחיר התחלתי')).toBeInTheDocument();

    await userEvent.keyboard('{Enter}');

    await waitFor(() => expect(screen.queryByText('מחיר התחלתי')).not.toBeInTheDocument());
    await userEvent.type(input, ' ');
    expect(await screen.findByText('מחיר התחלתי')).toBeInTheDocument();
  });
});
