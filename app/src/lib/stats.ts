// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
export function mean(xs: number[]): number {
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0;
}

export function stdev(xs: number[]): number {
  if (xs.length < 2) return 0;
  const m = mean(xs);
  return Math.sqrt(xs.reduce((a, x) => a + (x - m) ** 2, 0) / xs.length);
}

export function median(xs: number[]): number {
  if (!xs.length) return 0;
  const s = [...xs].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

/** the item with the greatest key ≤ date, else null */
export function latestOnOrBefore<T>(items: T[], date: string, key: (t: T) => string): T | null {
  let best: T | null = null;
  for (const it of items) {
    // >= so equal-key ties keep the LAST item in input order — balance series
    // are sorted (as_of, source) and the later source is the fresher one
    if (key(it) <= date && (best === null || key(it) >= key(best))) best = it;
  }
  return best;
}

/** least-squares slope of y over x (x as 0..n-1); returns slope per x-unit */
export function slope(ys: number[]): number {
  const n = ys.length;
  if (n < 2) return 0;
  const xm = (n - 1) / 2;
  const ym = mean(ys);
  let num = 0, den = 0;
  for (let i = 0; i < n; i++) {
    num += (i - xm) * (ys[i] - ym);
    den += (i - xm) ** 2;
  }
  return den === 0 ? 0 : num / den;
}
