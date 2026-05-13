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
