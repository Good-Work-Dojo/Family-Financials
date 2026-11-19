// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fmtCompact, fmtDelta, fmtPct, fmtUSD, fmtUSD0 } from "./format";
import { isPrivate, MASK, maskMoneyText, PRIVACY_KEY, setPrivate, subscribePrivacy } from "./privacy";

afterEach(() => { vi.restoreAllMocks(); setPrivate(false); });

describe("privacy flag", () => {
  it("starts off, flips, notifies subscribers, and the money formatters mask while it is on", () => {
    expect(isPrivate()).toBe(false);
    let calls = 0;
    const off = subscribePrivacy(() => { calls++; });
    setPrivate(true);
    expect(isPrivate()).toBe(true);
    expect(calls).toBe(1);
    for (const s of [fmtUSD(1234.5), fmtUSD0(1234.5), fmtCompact(1_250_000), fmtDelta(-50)]) expect(s).toBe(MASK);
    expect(fmtPct(0.123)).toBe("12.3%");            // a ratio is not an amount
    off();
    setPrivate(false);
    expect(fmtUSD0(1234.5)).toBe("$1,235");
    expect(calls).toBe(1);
  });

  it("persists per device: on writes the key, off removes it", () => {
    setPrivate(true);
    expect(localStorage.getItem(PRIVACY_KEY)).toBe("1");
    setPrivate(false);
    expect(localStorage.getItem(PRIVACY_KEY)).toBeNull();
  });
});

describe("privacy across tabs", () => {
  // the browser fires `storage` in the OTHER tabs when this origin's storage changes - never in the tab that wrote
  it("a switch made in another tab flips this tab's flag, tells its subscribers and unmasks the formatters again", () => {
    let calls = 0;
    const off = subscribePrivacy(() => { calls++; });
    localStorage.setItem(PRIVACY_KEY, "1");                                   // the other tab turned it on...
    window.dispatchEvent(new StorageEvent("storage", { key: PRIVACY_KEY, newValue: "1" }));
    expect(calls).toBe(1);
    expect(isPrivate()).toBe(true);
    expect(fmtUSD0(1234.5)).toBe(MASK);
    localStorage.removeItem(PRIVACY_KEY);                                     // ...and off again
    window.dispatchEvent(new StorageEvent("storage", { key: PRIVACY_KEY, newValue: null }));
    expect(calls).toBe(2);
    expect(isPrivate()).toBe(false);
    off();
  });
  it("a cleared storage (the event's key is null) is re-read: nothing stored means off", () => {
    setPrivate(true);
    localStorage.clear();                                                      // what clearing site data does in another tab
    window.dispatchEvent(new StorageEvent("storage", { key: null }));
    expect(isPrivate()).toBe(false);
  });
  it("a storage that cannot be read keeps a masked page masked - it never fails open", () => {
    setPrivate(true);
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("denied"); });
    let calls = 0;
    const off = subscribePrivacy(() => { calls++; });
    window.dispatchEvent(new StorageEvent("storage", { key: PRIVACY_KEY }));
    window.dispatchEvent(new StorageEvent("storage", { key: null }));
    expect(isPrivate()).toBe(true);
    expect(fmtUSD0(1234.5)).toBe(MASK);
    expect(calls).toBe(2);
    off();
  });
  it("is read from storage once and cached: a later write to storage with no event changes nothing", () => {
    expect(isPrivate()).toBe(false);
    localStorage.setItem(PRIVACY_KEY, "1");                                    // no storage event: this tab was not told
    expect(isPrivate()).toBe(false);
    localStorage.removeItem(PRIVACY_KEY);
  });
  it("another key's change is not a privacy change", () => {
    let calls = 0;
    const off = subscribePrivacy(() => { calls++; });
    window.dispatchEvent(new StorageEvent("storage", { key: "theme", newValue: "light" }));
    expect(calls).toBe(0);
    off();
  });
});

describe("maskMoneyText (free text the ETL wrote)", () => {
  const detail = "store sum $12,345.67 vs raw-file sum -$12,345.67 (drift \u2212$0.40; pending $5K, about $1.2M)";
  const masked = (s: string) => { setPrivate(true); return maskMoneyText(s); };
  it("leaves the text alone while privacy mode is off", () => {
    expect(maskMoneyText(detail)).toBe(detail);
    expect(maskMoneyText("net 1,234.56")).toBe("net 1,234.56");
  });
  it("replaces every currency-marked amount (signed, with cents, K / M) and keeps the words", () => {
    expect(masked(detail)).toBe(`store sum ${MASK} vs raw-file sum ${MASK} (drift ${MASK}; pending ${MASK}, about ${MASK})`);
  });
  it("masks the magnitude too: $1.2k, $1.2K, $1.2 million, $3 billion, $2B, $.50, USD 1,234, EUR 5, \u20ac1,234, \u00a312.50", () => {
    for (const t of ["$1.2k", "$1.2K", "$1.2 million", "$1.2M", "$3 billion", "$2B", "$.50", "$5 thousand", "USD 1,234", "usd 5", "EUR 5", "\u20ac1,234", "\u00a312.50", "-$5", "\u2212$5.00"]) {
      expect(masked(`worth ${t} today`), t).toBe(`worth ${MASK} today`);
    }
  });
  // etl/enrich/transfers.py writes each data gap's context as `{first} -> {last}, net {total / 100:,.2f}` - NO dollar sign
  it("masks an amount with no currency symbol: the exact data-gap shape, negatives, grouped integers and two-decimal numbers", () => {
    expect(masked("2024-02-01 \u2192 2026-09-05, net 1,234.56")).toBe(`2024-02-01 \u2192 2026-09-05, net ${MASK}`);
    expect(masked("2024-02-01 \u2192 2026-09-05, net -1,234.56")).toBe(`2024-02-01 \u2192 2026-09-05, net ${MASK}`);
    expect(masked("2024-02-01 \u2192 2026-09-05, net \u22121,234.56")).toBe(`2024-02-01 \u2192 2026-09-05, net ${MASK}`);
    expect(masked("net 12,345,678.90")).toBe(`net ${MASK}`);
    expect(masked("net 1,234")).toBe(`net ${MASK}`);
    expect(masked("net 123.45")).toBe(`net ${MASK}`);
    expect(masked("net -0.40")).toBe(`net ${MASK}`);
    expect(masked("net 1,234.56, then 2,345.67 and (3,456.78)")).toBe(`net ${MASK}, then ${MASK} and (${MASK})`);
    expect(masked("1,234.56")).toBe(MASK);
    expect(masked("x-1,234.56")).toBe(`x-${MASK}`);
  });
  // "(?:USD|EUR|GBP)" inside a word is not a currency code: CHAUFFEUR 0412, Amateur 2026
  it("a currency CODE is a whole word: it masks \"USD 1,234\" and \"USD1,234\" but not the end of CHAUFFEUR or Amateur, nor USDA / Eurostar", () => {
    expect(masked("CHAUFFEUR 0412")).toBe("CHAUFFEUR 0412");
    expect(masked("Amateur 2026")).toBe("Amateur 2026");
    expect(masked("USDA 5 and Eurostar 2026 and MUSD 5")).toBe("USDA 5 and Eurostar 2026 and MUSD 5");
    expect(masked("USD 1,234")).toBe(MASK);
    expect(masked("USD1,234")).toBe(MASK);
    expect(masked("paid EUR 5 for GBP 12.50 of CHAUFFEUR 0412")).toBe(`paid ${MASK} for ${MASK} of CHAUFFEUR 0412`);
  });
  it("touches nothing that is not an amount: counts, dates, years, ids, percentages, versions, a bare dollar sign", () => {
    for (const s of ["12 of 14 bank Venmo debits linked", "2026-01-05 \u2192 2026-02-01", "2026-05", "12/31", "2026-05-01 \u2192 2026-05-31",
      "3.2% of gross spending", "+0.52% off the lender", "12.50%", "12.50 %", "1,234.5%", "price in $ only", "version 2.0", "v1.2.3",
      "acct ...0101", "SimpleFIN id 123456", "3 internal rows in demo_checking_0101", "pi 3.14159", "ratio 1.5x"]) {
      expect(masked(s), s).toBe(s);
    }
  });
});

// No section may format money by hand: a `toFixed(2)` dollar string would bypass the mask. The
// percentage/months/count uses of toFixed elsewhere keep a different digit count on purpose.
const SRC = dirname(fileURLToPath(import.meta.url));
const walk = (dir: string): string[] => readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
  e.isDirectory() ? walk(join(dir, e.name)) : /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [join(dir, e.name)] : []);
const FORMAT_TS = join(SRC, "format.ts");
const rel = (f: string) => f.slice(SRC.length - 3);
describe("no hand-formatted money", () => {
  const surfaces = () => [...walk(join(SRC, "..", "sections")), ...walk(join(SRC, "..", "components"))];
  it("sections/** and components/** contain no toFixed(2)", () => {
    const files = surfaces();
    expect(files.length).toBeGreaterThan(20);
    const hits = files.filter((f) => /toFixed\(2\)/.test(readFileSync(f, "utf8")));
    expect(hits.map(rel)).toEqual([]);
  });
  it("nothing outside lib/format.ts builds a currency string of its own (Intl currency style, a `$${` template)", () => {
    const files = [...surfaces(), ...walk(SRC).filter((f) => f !== FORMAT_TS)];
    const hits = files.filter((f) => /style:\s*["']currency["']|\$\$\{/.test(readFileSync(f, "utf8")));
    expect([...new Set(hits)].map(rel)).toEqual([]);
  });
  it("no chart formatter prints a raw `{c}` value (it would bypass the mask): money labels go through fmtUSD0 / fmtCompact", () => {
    const hits = surfaces().filter((f) => /formatter:\s*["'`][^"'`]*\{c\}/.test(readFileSync(f, "utf8")));
    expect(hits.map(rel)).toEqual([]);
  });
});
