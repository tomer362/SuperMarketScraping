import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import * as api from '../api';
import SearchPage from '../pages/SearchPage';
import { renderWithQueryClient } from './render';

describe('SearchPage', () => {
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
});
