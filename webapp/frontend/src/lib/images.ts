const INVALID_IMAGE_MARKERS = [
  'default',
  'no-image',
  'no_image',
  'noimage',
  'placeholder',
];

export function displayableImageUrl(imageUrl?: string | null): string | null {
  const value = imageUrl?.trim();
  if (!value) {
    return null;
  }

  const lowered = value.toLowerCase();
  if (INVALID_IMAGE_MARKERS.some((marker) => lowered.includes(marker))) {
    return null;
  }

  return value;
}
