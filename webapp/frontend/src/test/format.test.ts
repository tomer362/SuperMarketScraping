import { describe, expect, it } from 'vitest';
import { formatComparisonUnit, formatDistance } from '../lib/format';
import { parseSearchQuantity } from '../lib/queryQuantity';

describe('formatComparisonUnit', () => {
  it('labels comparable unit prices without the base placeholder', () => {
    expect(formatComparisonUnit({ is_weighable: true, unit_dimension: 'mass' })).toBe('לק״ג');
    expect(formatComparisonUnit({ is_weighable: false, unit_dimension: 'mass' })).toBe('ל-100 גרם');
    expect(formatComparisonUnit({ is_weighable: false, unit_dimension: 'volume' })).toBe('ל-100 מ״ל');
    expect(formatComparisonUnit({ is_weighable: false, unit_dimension: 'count' })).toBe('ליחידה');
    expect(formatComparisonUnit({ is_weighable: false, unit_dimension: null })).toBeNull();
  });
});

describe('formatDistance', () => {
  it('formats nearby store distances and hides missing values', () => {
    expect(formatDistance(null)).toBeNull();
    expect(formatDistance(0.4)).toBe('400 מ׳ ממך');
    expect(formatDistance(3.4)).toBe('3.4 ק״מ ממך');
  });
});

describe('parseSearchQuantity', () => {
  it('extracts Hebrew metric quantities and cleans the product query', () => {
    expect(parseSearchQuantity('סלמון 500 גרם')).toEqual({
      cleanedQuery: 'סלמון',
      quantity: { original: '500 גרם', value: 0.5, dimension: 'mass' },
    });
    expect(parseSearchQuantity('סלמון 1 ק״ג')).toEqual({
      cleanedQuery: 'סלמון',
      quantity: { original: '1 ק״ג', value: 1, dimension: 'mass' },
    });
    expect(parseSearchQuantity('מים 750 מ״ל')).toEqual({
      cleanedQuery: 'מים',
      quantity: { original: '750 מ״ל', value: 0.75, dimension: 'volume' },
    });
    expect(parseSearchQuantity('ביצים 12 יחידות')).toEqual({
      cleanedQuery: 'ביצים',
      quantity: { original: '12 יחידות', value: 12, dimension: 'count' },
    });
  });
});
