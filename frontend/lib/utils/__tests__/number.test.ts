import { describe, it, expect } from 'vitest';
import { formatNumber, formatNumberWithDecimals } from '../number';

describe('formatNumber', () => {
  it('formats small numbers without grouping', () => {
    expect(formatNumber(0)).toBe('0');
    expect(formatNumber(999)).toBe('999');
  });

  it('formats thousands with commas', () => {
    expect(formatNumber(1000)).toBe('1,000');
    expect(formatNumber(1234)).toBe('1,234');
  });

  it('formats millions with commas', () => {
    expect(formatNumber(1234567)).toBe('1,234,567');
  });

  it('formats negative numbers', () => {
    expect(formatNumber(-1234)).toBe('-1,234');
  });

  it('formats decimal numbers', () => {
    // toLocaleString by default truncates, but the result should still have grouping
    expect(formatNumber(1234.56)).toBe('1,234.56');
  });
});

describe('formatNumberWithDecimals', () => {
  it('formats with custom decimal places', () => {
    const result = formatNumberWithDecimals(1234.5, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    expect(result).toBe('1,234.50');
  });

  it('formats without options like formatNumber', () => {
    expect(formatNumberWithDecimals(1000)).toBe('1,000');
  });

  it('formats percentages', () => {
    const result = formatNumberWithDecimals(0.856, {
      style: 'percent',
      minimumFractionDigits: 1,
    });
    expect(result).toBe('85.6%');
  });

  it('formats currency', () => {
    const result = formatNumberWithDecimals(1234.5, {
      style: 'currency',
      currency: 'USD',
    });
    expect(result).toBe('$1,234.50');
  });
});
