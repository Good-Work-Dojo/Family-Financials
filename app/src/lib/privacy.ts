// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
/** Privacy mode hides AMOUNTS (not names, dates, counts or percentages) everywhere money is formatted:
 *  the four money formatters in lib/format.ts return MASK while it is on, so axes, tooltips, KPIs,
 *  tables and inputs at rest go dark together. Per device (localStorage, like the theme). */
import { storageKey } from "./demo";

export const PRIVACY_KEY = storageKey("ff.privacy");
export const MASK = "•••••";

// The flag is a cached module variable (every formatted number asks for it): read from storage once at import,
// changed by setPrivate, and kept in step with other tabs by ONE storage listener. A storage that cannot be
// read leaves the flag as it is, so a page that is masked stays masked - it never fails open.
function read(fallback: boolean): boolean {
  try { return typeof localStorage !== "undefined" && localStorage.getItem(PRIVACY_KEY) === "1"; } catch { return fallback; }
}
let on = read(false);
const listeners = new Set<() => void>();

export function isPrivate(): boolean { return on; }
export function setPrivate(v: boolean): void {
  on = v;
  try { if (v) localStorage.setItem(PRIVACY_KEY, "1"); else localStorage.removeItem(PRIVACY_KEY); } catch { /* memory only */ }
  listeners.forEach((l) => l());
}
export function subscribePrivacy(cb: () => void): () => void {
  listeners.add(cb);
  return () => { listeners.delete(cb); };
}
// another tab's switch (the event never fires in the tab that wrote); key null = the storage was cleared
if (typeof window !== "undefined") {
  window.addEventListener("storage", (e) => {
    if (e.key !== null && e.key !== PRIVACY_KEY) return;
    on = read(on);
    listeners.forEach((l) => l());
  });
}

// Free text the ETL (and a bank's own transaction description) wrote states amounts in its own words, so no formatter ever saw
// them. Three shapes: a currency-marked amount ("$1,234.56", "-$5", "$1.2k", "$1.2 million", "$.50", "USD 1,234",
// "EUR 5", "\u20ac1,234"; a code is a whole word - "CHAUFFEUR 0412", "Amateur 2026" are not money); and amounts the ETL prints with NO symbol (the data gaps: `net {total:,.2f}`) - a grouped
// number ("1,234", "-1,234.56") or a two-decimal one ("12.50"). A number followed by "%" is a ratio, never an amount;
// dates, years and ids ("2026-05", "12/31", "2026-05-01") have neither shape. No lookbehind (Safari before 16.4 throws
// on the regex literal): the character before an unprefixed number is captured and put back.
const CURRENCY_AMOUNT = new RegExp(
  String.raw`[-\u2212]?(?:[$\u20ac\u00a3]|\b(?:USD|EUR|GBP)(?![A-Za-z])\s?)\s?(?:\d[\d,]*(?:\.\d+)?|\.\d+)(?:\s?(?:million|billion|thousand|[kmb])\b)?`, "gi");
const GROUPED = /(^|[^\w.,])([-\u2212]?\d{1,3}(?:,\d{3})+(?:\.\d+)?)(?!\w|,\d|\.\d|\s?%)/g;
const TWO_DECIMALS = /(^|[^\w.,])([-\u2212]?\d+\.\d\d)(?!\w|\d|\.\d|\s?%)/g;

/** Free text from the ETL (a quality check's detail, an open question or data gap, a transaction description): while privacy mode is
 *  on each amount becomes MASK and the words stay. Pass such text through this wherever it is shown (a title or a
 *  tooltip too); never use it on a number - that is what the formatters are for. */
export function maskMoneyText(s: string): string {
  if (!isPrivate()) return s;
  return s.replace(CURRENCY_AMOUNT, MASK)
    .replace(GROUPED, (_m, pre: string) => pre + MASK)
    .replace(TWO_DECIMALS, (_m, pre: string) => pre + MASK);
}
