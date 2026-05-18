export function formatCurrency(value: number | null | undefined): string {
  if (value == null) {
    return '-';
  }
  return new Intl.NumberFormat('he-IL', {
    style: 'currency',
    currency: 'ILS',
    maximumFractionDigits: 2,
  }).format(value);
}

export function formatRelativeDate(value: string | null | undefined): string {
  if (!value) {
    return 'לא זמין';
  }
  return new Date(value).toLocaleString('he-IL', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatQuantity(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) {
    return '-';
  }
  if (Number.isInteger(value)) {
    return value.toLocaleString('he-IL');
  }
  return value.toLocaleString('he-IL', {
    minimumFractionDigits: 1,
    maximumFractionDigits: 2,
  });
}

export function formatComparisonUnit(input: {
  is_weighable?: boolean | null;
  unit_dimension?: string | null;
}): string | null {
  if (input.is_weighable) {
    if (input.unit_dimension === 'mass') {
      return 'לק״ג';
    }
    if (input.unit_dimension === 'volume') {
      return 'לל׳';
    }
    return null;
  }
  if (input.unit_dimension === 'mass') {
    return 'ל-100 גרם';
  }
  if (input.unit_dimension === 'volume') {
    return 'ל-100 מ״ל';
  }
  if (input.unit_dimension === 'count') {
    return 'ליחידה';
  }
  return null;
}

export function formatQuantityWithUnit(value: number | null | undefined, dimension?: string | null): string {
  if (value == null) {
    return '-';
  }
  if (dimension === 'mass') {
    return `${formatQuantity(value)} ק״ג`;
  }
  if (dimension === 'volume') {
    return `${formatQuantity(value)} ל׳`;
  }
  return `${formatQuantity(value)} יח׳`;
}
