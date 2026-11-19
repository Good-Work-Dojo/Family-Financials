// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// The privacy net's allowances (test/moneyScan.ts) against the source tree: every allowance matches something the
// application prints. A COUNTED_NOUNS entry is a noun the app prints after a grouped count; this reads where the tree
// prints counts - each `countOf(n, "noun")` call and each number formatted with `toLocaleString` that a word follows -
// and writes what each would print at 1,234, so a noun no longer printed that way (or only after an ungrouped number,
// which no grouped pattern can match) is found as dead. The route scans in the smoke suite check the same allowances
// against what the demo household's pages print; this check needs no exports.
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { AMOUNTS, COUNTED_NOUNS, STATIC_OK, STATIC_OK_PARTS } from "./moneyScan";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");
/** the application's sources: no test file and nothing under test/ */
const files = (dir: string): string[] => readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
  e.isDirectory() ? (join(dir, e.name) === join(SRC, "test") ? [] : files(join(dir, e.name)))
  : /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [join(dir, e.name)] : []);
const SOURCES = files(SRC).map((f) => readFileSync(f, "utf8"));

/** the index just past the parenthesis that closes the one opening at `open` */
function closing(src: string, open: number): number {
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    if (src[i] === "(") depth++;
    else if (src[i] === ")" && --depth === 0) return i + 1;
  }
  return src.length;
}

const N = (1234).toLocaleString("en-US");
/** what a `countOf(...)` call starting at `at` prints with a count of 1,234 ("1,234 transactions"); null when its noun
 *  is no literal */
function countOfSample(src: string, at: number): string | null {
  const call = src.slice(at, closing(src, at + "countOf".length));
  const words = /,\s*"([^"]+)"\s*(?:,\s*"([^"]+)"\s*)?\)$/.exec(call);
  return words ? `${N} ${words[2] ?? `${words[1]}s`}` : null;
}

/** every count the tree prints, as it reads at 1,234: each countOf call (a count whose noun is "of" followed by another
 *  countOf, the pair: "1,234 of 1,234 transactions"), and each toLocaleString'd number with the words after it (when
 *  those are "of" and another countOf, the whole "1,234 of 1,234 transactions") */
function printedCounts(src: string): string[] {
  const out: string[] = [];
  for (const m of src.matchAll(/\bcountOf\(/g)) {
    const s = countOfSample(src, m.index!);
    if (s === null) continue;
    const after = src.slice(closing(src, m.index! + "countOf".length));
    const next = s === `${N} of` ? /^\}?\s*\{?countOf\(/.exec(after) : null;
    const pair = next ? countOfSample(after, next[0].length - "countOf(".length) : null;
    out.push(pair !== null ? `${s} ${pair}` : s);
  }
  for (const m of src.matchAll(/\.toLocaleString\([^)]*\)\}?/g)) {
    const after = src.slice(m.index! + m[0].length);
    const of = /^\s*of\s*\$?\{countOf\(/.exec(after);
    if (of) {
      const s = countOfSample(after, of[0].length - "countOf(".length);
      if (s !== null) out.push(`${N} of ${s}`);
      continue;
    }
    // the words up to the end of the phrase (a closing bracket, a tag or an interpolation), commas included
    const words = /^\s*([a-z][a-z ,-]*)/.exec(after);
    if (words) out.push(`${N} ${words[1].trim()}`);
  }
  return out;
}

describe("the privacy net's allowances against the source tree", () => {
  const counts = SOURCES.flatMap(printedCounts);

  it("reads the tree's counts (the reader is not vacuous)", () => {
    expect(SOURCES.length).toBeGreaterThan(150);
    expect(counts).toContain(`${N} transactions`);
    expect(counts).toContain(`${N} of ${N} transactions`);
    expect(counts).toContain(`${N} left, smaller amounts`);
    expect(printedCounts('x = countOf(a.reduce((n, s) => n + s, 0), "row"); y = countOf(b, "child", "children");'))
      .toEqual([`${N} rows`, `${N} children`]);
    expect(printedCounts("{n.toLocaleString()} left, smaller amounts)</span>")).toEqual([`${N} left, smaller amounts`]);
    expect(printedCounts('{countOf(n, "of", "of")} {countOf(m, "transaction")}')).toEqual([`${N} of ${N} transactions`, `${N} transactions`]);
    expect(printedCounts("{n} vendors")).toEqual([]);                               // an ungrouped number prints no count
  });

  it("every counted noun is one the app prints after a grouped count", () => {
    const dead = COUNTED_NOUNS.filter((noun) => !counts.some((c) => new RegExp(String.raw`^\d{1,3}(?:,\d{3})+ (?:${noun})\b`).test(c)));
    expect(dead, "nouns no source prints after a grouped count: the allowance can never match").toEqual([]);
  });

  it("every other allowance matches something the tree prints", () => {
    const printed = [...counts, ...SOURCES];
    const dead = STATIC_OK_PARTS.filter((p) => !printed.some((s) => new RegExp(p).test(s)));
    expect(dead).toEqual([]);
  });

  it("the nouns the net lacked are allowed, and a noun printed only after an ungrouped number is not", () => {
    const allowed = new RegExp(STATIC_OK_PARTS[1]);
    expect(allowed.test(`${N} earlier month-ends`)).toBe(true);
    expect(allowed.test(`(${N} left, smaller amounts)`)).toBe(true);
    expect(allowed.test(`${N} vendors`), "learned vendors are counted ungrouped").toBe(false);
    expect(allowed.test(`${N} purchases`), "no page counts purchases").toBe(false);
  });

  it("an amount stays an amount whatever word follows it: no allowance blanks a number after a currency sign", () => {
    // what the net would still see in a string once the allowances are blanked out
    const seen = (s: string) => AMOUNTS.some((re) => new RegExp(re.source).test(s.replace(STATIC_OK, " ")));
    for (const leak of [`$${N} left to spend`, `$${N} transactions`, `€ ${N} rows`, `£${N} of ${N} transactions`,
      `${N} left to spend`]) {
      expect(seen(leak), leak).toBe(true);
    }
    // and the counts the app prints stay quiet
    for (const count of [`${N} transactions`, `(${N} left, smaller amounts)`, `${N} of ${N} transactions`,
      `Data quality — ${N} warnings · 1 open question`]) {
      expect(seen(count), count).toBe(false);
    }
  });
});
