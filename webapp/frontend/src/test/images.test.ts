import { describe, expect, it } from 'vitest';
import { displayableImageUrl } from '../lib/images';

describe('displayableImageUrl', () => {
  it('hides missing and default placeholder images', () => {
    expect(displayableImageUrl(null)).toBeNull();
    expect(displayableImageUrl('')).toBeNull();
    expect(displayableImageUrl('https://media.shufersal.co.il/product_images/default/L_P_default.png')).toBeNull();
    expect(displayableImageUrl('https://example.com/no-image.png')).toBeNull();
  });

  it('keeps real product image URLs', () => {
    expect(displayableImageUrl('https://img.rami-levy.co.il/product/7290004131074/small.jpg')).toBe(
      'https://img.rami-levy.co.il/product/7290004131074/small.jpg',
    );
  });
});
