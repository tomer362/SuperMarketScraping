import { screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api';
import GroupPage from '../pages/GroupPage';
import { renderWithQueryClient } from './render';

describe('GroupPage', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders generic group offers and add-to-list action', async () => {
    vi.spyOn(api, 'getGenericGroupDetail').mockResolvedValue({
      key: 'milk|3pct|1000ml',
      label: 'חלב 3% 1 ליטר',
      family: 'milk',
      offer_count: 4,
      chain_count: 2,
      cheapest_price: 5.5,
      offers: [
        {
          id: 1,
          chain: 'carrefour',
          chain_label: 'קרפור',
          store_id: '1',
          store_name: 'קרפור תל אביב',
          product_id: 'milk-1',
          name: 'חלב טרה 3% 1 ליטר',
          price: 5.5,
          regular_price: 6,
          sale_price: null,
          discount_percent: null,
          price_per_base_unit: 5.5,
          brand: 'טרה',
          image_url: null,
          deal: null,
          scraped_at: '2026-05-18T00:00:00Z',
        },
        {
          id: 2,
          chain: 'ybitan',
          chain_label: 'יינות ביתן',
          store_id: '2',
          store_name: 'יינות ביתן רמת גן',
          product_id: 'milk-2',
          name: 'חלב תנובה 3% 1 ליטר',
          price: 5.9,
          regular_price: 5.9,
          sale_price: null,
          discount_percent: null,
          price_per_base_unit: 5.9,
          brand: 'תנובה',
          image_url: null,
          deal: null,
          scraped_at: '2026-05-18T00:00:00Z',
        },
      ],
    });

    renderWithQueryClient(
      <MemoryRouter initialEntries={['/groups/milk%7C3pct%7C1000ml']}>
        <Routes>
          <Route path="/groups/:groupKey" element={<GroupPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText('חלב 3% 1 ליטר')).toBeInTheDocument();
    expect(screen.getByText('חלב טרה 3% 1 ליטר')).toBeInTheDocument();
    expect(screen.getByText('חלב תנובה 3% 1 ליטר')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'הוסף לרשימה' })).toBeInTheDocument();
    expect(api.getGenericGroupDetail).toHaveBeenCalledWith('milk|3pct|1000ml');
  });
});
