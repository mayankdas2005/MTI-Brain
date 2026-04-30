/**
 * Force three-digit grouping (1,234,567) regardless of the user's browser
 * locale. Without an explicit locale, `toLocaleString()` follows the
 * system setting - en-IN renders 12,34,567, which is wrong for finance UIs
 * targeting an international audience.
 */
const NUMBER_LOCALE = 'en-US';

export function formatNumber(value: number): string {
  return value.toLocaleString(NUMBER_LOCALE);
}

export function formatNumberWithDecimals(
  value: number,
  options?: Intl.NumberFormatOptions,
): string {
  return value.toLocaleString(NUMBER_LOCALE, options);
}
