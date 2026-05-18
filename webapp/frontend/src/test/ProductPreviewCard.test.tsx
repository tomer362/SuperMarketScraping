import { fireEvent, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import ProductPreviewCard from '../components/ProductPreviewCard';
import { renderWithQueryClient } from './render';

describe('ProductPreviewCard', () => {
  it('renders key product data', () => {
    renderWithQueryClient(
      <MemoryRouter>
        <ProductPreviewCard
          product={{
            id: 7,
            name: 'קוטג׳ תנובה 250 גרם',
            brand: 'תנובה',
            manufacturer: 'תנובה',
            barcode: '7290333333333',
            image_url: null,
            unit_description: '250 גרם',
            unit_of_measure: 'גרם',
            unit_qty: 250,
            unit_qty_si: 250,
            unit_dimension: 'mass',
            is_weighable: false,
            cheapest_price: 5.5,
            cheapest_chain: 'ramilevi',
            cheapest_chain_label: 'רמי לוי',
            cheapest_store_name: 'רמי לוי מודיעין',
            chain_count: 2,
            has_deal: true,
          }}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText('קוטג׳ תנובה 250 גרם')).toBeInTheDocument();
    expect(screen.getByText('יש מבצע')).toBeInTheDocument();
    expect(screen.getByText('רמי לוי')).toBeInTheDocument();
    expect(screen.queryByText('🛒')).not.toBeInTheDocument();
  });

  it('hides broken product images after the browser reports a load error', () => {
    renderWithQueryClient(
      <MemoryRouter>
        <ProductPreviewCard
          product={{
            id: 8,
            name: 'חלב 13% ליטר',
            brand: null,
            manufacturer: null,
            barcode: null,
            image_url: 'https://example.com/dead-product-image.jpg',
            unit_description: '1 ליטר',
            unit_of_measure: 'ליטר',
            unit_qty: 1,
            unit_qty_si: 1,
            unit_dimension: 'volume',
            is_weighable: false,
            cheapest_price: 5.9,
            cheapest_chain: 'carrefour',
            cheapest_chain_label: 'קרפור',
            cheapest_store_name: 'קרפור תל אביב',
            chain_count: 1,
            has_deal: false,
          }}
        />
      </MemoryRouter>,
    );

    fireEvent.error(screen.getByAltText('חלב 13% ליטר'));

    expect(screen.queryByAltText('חלב 13% ליטר')).not.toBeInTheDocument();
  });
});
