export type ParsedQuantityDimension = 'mass' | 'volume' | 'count';

export interface ParsedQuantity {
  original: string;
  value: number;
  dimension: ParsedQuantityDimension;
}

export interface ParsedSearchQuery {
  cleanedQuery: string;
  quantity: ParsedQuantity | null;
}

const QUANTITY_RE =
  /(?:^|\s)(\d+(?:[.,]\d+)?)\s*(ק["״']?ג|קילו(?:גרם)?|קילוגרם|גרם|ג(?:ר)?׳?|ליטר|ל["״']?|מ["״']?ל|מיליליטר|יחידות|יחידה|יח׳|יח)(?=\s|$)/i;

function normalizeNumber(value: string): number | null {
  const parsed = Number(value.replace(',', '.'));
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

export function parseSearchQuantity(query: string): ParsedSearchQuery {
  const match = query.match(QUANTITY_RE);
  if (!match || match.index == null) {
    return { cleanedQuery: query.trim(), quantity: null };
  }

  const numericValue = normalizeNumber(match[1]);
  if (numericValue == null) {
    return { cleanedQuery: query.trim(), quantity: null };
  }

  const rawUnit = match[2].replace(/[״"']/g, '').toLowerCase();
  let quantity: ParsedQuantity | null = null;
  if (rawUnit === 'גרם' || rawUnit === 'גר' || rawUnit === 'ג' || rawUnit === 'גר׳') {
    quantity = { original: match[0].trim(), value: numericValue / 1000, dimension: 'mass' };
  } else if (rawUnit === 'קג' || rawUnit === 'קילו' || rawUnit === 'קילוגרם') {
    quantity = { original: match[0].trim(), value: numericValue, dimension: 'mass' };
  } else if (rawUnit === 'מל' || rawUnit === 'מיליליטר') {
    quantity = { original: match[0].trim(), value: numericValue / 1000, dimension: 'volume' };
  } else if (rawUnit === 'ל' || rawUnit === 'ליטר') {
    quantity = { original: match[0].trim(), value: numericValue, dimension: 'volume' };
  } else if (rawUnit.startsWith('יח')) {
    quantity = { original: match[0].trim(), value: numericValue, dimension: 'count' };
  }

  if (!quantity) {
    return { cleanedQuery: query.trim(), quantity: null };
  }

  const cleanedQuery = `${query.slice(0, match.index)} ${query.slice(match.index + match[0].length)}`
    .replace(/\s+/g, ' ')
    .trim();

  return {
    cleanedQuery: cleanedQuery || query.trim(),
    quantity,
  };
}
