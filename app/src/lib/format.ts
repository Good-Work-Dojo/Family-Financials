// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { clock, dayOf } from "./clock";
import { isPrivate, MASK } from "./privacy";

const usd = new Intl.NumberFormat("en-US", {
  style: "currency", currency: "USD", maximumFractionDigits: 2,
});
const usd0 = new Intl.NumberFormat("en-US", {
  style: "currency", currency: "USD", maximumFractionDigits: 0,
});

// The privacy mask lives HERE, in the four money formatters (fmtUSD, fmtUSD0, fmtCompact, fmtDelta): every
// money surface - KPI, table, axis, tooltip, label, accessible name - inherits it, and a new one cannot
// forget it. fmtPct, dates and counts are not amounts and never mask. A hand-built dollar string anywhere
// else would bypass the mask (lib/privacy.test.ts greps for them).

/** the one minus every money formatter prints: U+2212, the typographic minus fmtDelta always used */
const MINUS = "\u2212";
/** Intl writes a negative with a hyphen-minus: swap it for MINUS */
const signed = (s: string): string => (s.startsWith("-") ? MINUS + s.slice(1) : s);

/** A value that rounds to zero at `dp` decimals prints as a plain zero: IEEE -0 (what `-(0 * x)` makes) and a small
 *  negative like -0.3 at whole dollars would otherwise print "−$0", a minus sign on nothing. */
const unzero = (v: number, dp: number): number => (Math.abs(v) < 0.5 / 10 ** dp ? 0 : v);

export function fmtUSD(v: number): string {
  if (isPrivate()) return MASK;
  return signed(usd.format(unzero(v, 2)));
}
export function fmtUSD0(v: number): string {
  if (isPrivate()) return MASK;
  return signed(usd0.format(unzero(v, 0)));
}
/** an amount as it is used: whole dollars when it is whole to the cent, else to the cent ("$1,400", "$2,746.40") — a
 *  stated payment, a line's own factor; masked like the two it picks between */
export function fmtUSDExact(v: number): string {
  return Math.round(v * 100) % 100 === 0 ? fmtUSD0(v) : fmtUSD(v);
}
/** `v` in whole dollars as fmtUSD0 rounds it (half a dollar away from zero; never −0): a total of rows printed in whole
 *  dollars adds these, so it is the sum of the figures shown */
export const wholeDollars = (v: number): number => Math.sign(v) * Math.round(Math.abs(v)) || 0;
/** $1.2M / $450K style for axes and dense cards. Each unit is chosen by the value as that unit prints it, so a value that
 *  rounds up to the next unit is written in it: $1.00M, never $1000K; $1.0K, never $1000. */
export function fmtCompact(v: number): string {
  if (isPrivate()) return MASK;
  const a = Math.abs(v);
  const sign = v < 0 && a >= 0.5 ? MINUS : "";
  if (Math.round(a) < 1_000) return `${sign}$${Math.round(a)}`;
  const k = a / 1000;
  if (Number(k.toFixed(1)) < 10) return `${sign}$${k.toFixed(1)}K`;
  if (Math.round(k) < 1_000) return `${sign}$${Math.round(k)}K`;
  const m = a / 1_000_000;
  return `${sign}$${m.toFixed(Number(m.toFixed(2)) < 10 ? 2 : 1)}M`;
}
/** the share a change is of its base, for a "+$3,346 (0.3%)" chip: a whole percent, and one decimal under 1% so a real
 *  change never reads "(0%)" */
export function fmtChangeShare(share: number): string {
  return fmtPct(share, Math.abs(share) < 0.01 ? 1 : 0);
}
/** a compact signed change for a chart label: +$1.2K / −$450, a plain $0 for a change that rounds to nothing, and the mask
 *  ALONE (no sign in front of it) while privacy mode is on */
export function fmtCompactDelta(v: number): string {
  if (isPrivate()) return MASK;
  return `${v >= 0.5 ? "+" : ""}${fmtCompact(v)}`;
}
/** null = "no data for this window" (e.g. a rate over zero income) → "—".
 *  Never coerce a missing rate to 0 before calling this: 0.0% is a real value. */
export function fmtPct(v: number | null, dp = 1): string {
  if (v == null) return "—";
  const s = (v * 100).toFixed(dp);
  // a tiny negative rounds to "-0.0": a minus sign on nothing, as in the money formatters; a real negative prints
  // the same U+2212 minus the money formatters do, never a hyphen
  return `${Number(s) === 0 ? (0).toFixed(dp) : signed(s)}%`;
}
/** A signed gap between two percentages, in points: "+3.2 pts" / "\u22123.2 pts" - the one minus, and a plain "0.0 pts" when
 *  it rounds to nothing. A difference of ratios, so privacy mode leaves it, like fmtPct. */
export function fmtPoints(v: number, dp = 1): string {
  const s = Math.abs(v).toFixed(dp);
  return Number(s) === 0 ? `${(0).toFixed(dp)} pts` : `${v < 0 ? MINUS : "+"}${s} pts`;
}
/** An interest or return RATE given as a fraction: a whole percent when it is one, else one decimal - "11%", "9%", "8.8%",
 *  never "9%" for 8.75%. The one precision for every rate in prose (the margin APR on Investments and Planning -> Home). */
export function fmtRate(frac: number): string {
  const p = Math.round(frac * 1000) / 10;
  return `${Number.isInteger(p) ? p : p.toFixed(1)}%`;
}
/** the StatCard tone of a rate: green at or above 0, red below, and the NEUTRAL tone for null
 *  ("no data" is neither good nor bad - never `?? 0` it into green) */
export function rateTone(v: number | null): "pos" | "neg" | undefined {
  return v === null ? undefined : v >= 0 ? "pos" : "neg";
}
const months1 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
/** Months of cover (cash ÷ essential spend), "4.3 mo": one decimal, grouped, the U+2212 minus every amount prints,
 *  and "—" for null (nothing essential to divide by); `unit` "" for prose that says "months" itself. A RATIO, so -
 *  like fmtPct - privacy mode leaves it. */
export function fmtMonths(v: number | null, unit = " mo"): string {
  return v === null ? "—" : `${signed(months1.format(Math.abs(v) < 0.05 ? 0 : v))}${unit}`;   // -0.03 reads "0.0", not "−0.0"
}
/** A count and its noun, agreeing: "1 open question", "2 open questions", "6,198 transactions" (comma-grouped). `many`
 *  for a noun whose plural is not noun + "s". A count, not money, so privacy mode leaves it. */
export function countOf(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
}
/** signed, for deltas: +$1,234 / −$987; a change that rounds to nothing is a plain "$0", no sign on nothing */
export function fmtDelta(v: number): string {
  if (isPrivate()) return MASK;
  const z = unzero(v, 0);
  return `${z === 0 ? "" : z > 0 ? "+" : MINUS}${usd0.format(Math.abs(z))}`;
}
export function fmtDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-US", {
    month: "short", day: "numeric", year: "numeric",
  });
}
// A date written inside a sentence the ETL (or a bank) worded: an ISO date "2026-07-10", an ISO month "2026-07" or a
// US slash date "11/18/2019". The character before is captured and put back (no lookbehind: Safari before 16.4 throws on
// the regex literal, as in lib/privacy.ts). A token glued to a word, an underscore, a dot, a slash or a hyphen
// ("v2026-10", "tx_2026-10-01", "acct-2026-10") is an id or a version, and so is one a digit, a letter or a "T" follows
// ("2026-10-011", "2026-10-01T05:00:00Z").
const DATE_TOKEN = new RegExp(
  String.raw`(^|[^\w./-])(?:((?:19|20)\d{2})-(\d{2})-(\d{2})|((?:19|20)\d{2})-(\d{2})|(\d{1,2})/(\d{1,2})/((?:19|20)\d{2}))(?![\w/]|-\d|\.\d)`, "g");
const daysIn = (y: number, m: number) => new Date(y, m, 0).getDate();
const two = (n: number | string) => String(n).padStart(2, "0");

/** Render-time only: the dates in a sentence written for a machine's reader, spoken the way the app speaks them - an ISO
 *  date as `fmtDate` ("Jul 10, 2026"), an ISO month as `fmtMonth` ("Jul 2026"), a US slash date as `fmtDate` too.
 *  Free text from the ETL (a quality check's detail, an open question) and a holding's name read in one form this way;
 *  the export itself is never rewritten. Never call it on an id, a code or a CSV cell. A token that is not a real date
 *  ("2026-13-45", "2026-02-31") stays as written. */
export function humanizeDates(text: string): string {
  return text.replace(DATE_TOKEN, (whole, pre: string, y1, m1, d1, y2, m2, m3, d3, y3) => {
    if (y1 !== undefined) {
      const [y, m, d] = [Number(y1), Number(m1), Number(d1)];
      return m >= 1 && m <= 12 && d >= 1 && d <= daysIn(y, m) ? pre + fmtDate(`${y1}-${m1}-${d1}`) : whole;
    }
    if (y2 !== undefined) return Number(m2) >= 1 && Number(m2) <= 12 ? pre + fmtMonth(`${y2}-${m2}`) : whole;
    const [y, m, d] = [Number(y3), Number(m3), Number(d3)];
    return m >= 1 && m <= 12 && d >= 1 && d <= daysIn(y, m) ? pre + fmtDate(`${y}-${two(m)}-${two(d)}`) : whole;
  });
}
// Clock times are the household's: an instant is printed in the household's zone (`assumptions.household.timezone`;
// null, or a zone this browser's Intl refuses, is the browser's own, as `clock.today` reads it), so a browser in another
// zone shows the same times as one at home. The words are English; the 12- or 24-hour clock is the reader's own
// convention (the browser's locale). One formatter per zone and shape, made on first use.

/** the reader's clock convention, from the browser's locale: true for a 12-hour clock */
let readerTwelve: boolean | null = null;
function twelveHour(): boolean {
  if (readerTwelve === null) {
    const cycle = new Intl.DateTimeFormat(undefined, { hour: "numeric" }).resolvedOptions().hourCycle;
    readerTwelve = cycle !== "h23" && cycle !== "h24";
  }
  return readerTwelve;
}
type Shape = "dateTime" | "dateTimeYear" | "time" | "day" | "dayYear";
const SHAPES: Record<Shape, Intl.DateTimeFormatOptions> = {
  dateTime: { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" },
  dateTimeYear: { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" },
  time: { hour: "numeric", minute: "2-digit" },
  day: { month: "short", day: "numeric" },
  dayYear: { year: "numeric", month: "short", day: "numeric" },
};
const zoneFormats = new Map<string, Intl.DateTimeFormat>();
function zoneFormat(zone: string | null, shape: Shape): Intl.DateTimeFormat {
  const key = `${zone ?? ""}|${shape}`;
  let format = zoneFormats.get(key);
  if (format === undefined) {
    const opts: Intl.DateTimeFormatOptions = { ...SHAPES[shape] };
    if (opts.hour !== undefined) opts.hourCycle = twelveHour() ? "h12" : "h23";
    try {
      format = new Intl.DateTimeFormat("en-US", zone === null ? opts : { ...opts, timeZone: zone });
    } catch {
      format = new Intl.DateTimeFormat("en-US", opts);   // a zone this browser's table lacks: its own, never a throw
    }
    zoneFormats.set(key, format);
  }
  return format;
}
/** `at` is in another year than `now`, both read in `zone` */
const otherYear = (at: Date, now: Date, zone: string | null) => dayOf(at, zone).slice(0, 4) !== dayOf(now, zone).slice(0, 4);

/** "Aug 10, 5:00 PM CDT": an ISO instant on the household's clock (`zone`), with the zone's abbreviation from Intl, and
 *  "Aug 10, 2025, 5:00 PM CDT" when it falls in another year than the household's current one. Needs a real instant:
 *  the manifest's feed stamp is UTC with a Z and generated_at carries its offset, so either parses the same anywhere. */
export function fmtDateTime(iso: string, zone: string | null, now: Date = clock.now()): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "unknown";
  return zoneFormat(zone, otherYear(d, now, zone) ? "dateTimeYear" : "dateTime").format(d);
}
/** "5:00 PM" (or "17:00" for a reader on a 24-hour clock): the clock time of an instant in `zone`, no zone named */
export function fmtTime(at: Date, zone: string | null): string {
  return zoneFormat(zone, "time").format(at);
}
/** A zone as words, from Intl: its generic name ("Central Time"), else its long one at `at` ("Coordinated Universal
 *  Time" where the generic is only an offset, or in a browser with no generic names); null is the browser's own zone. */
export function zoneName(zone: string | null, at: Date = clock.now()): string {
  const nameIn = (style: "longGeneric" | "long"): string | null => {
    try {
      const f = new Intl.DateTimeFormat("en-US", { timeZoneName: style, ...(zone === null ? {} : { timeZone: zone }) });
      return f.formatToParts(at).find((p) => p.type === "timeZoneName")?.value ?? null;
    } catch {
      return null;   // a style or a zone this browser does not know
    }
  };
  const generic = nameIn("longGeneric");
  if (generic !== null && !/^GMT[+-]/.test(generic)) return generic;
  return nameIn("long") ?? generic ?? zone ?? Intl.DateTimeFormat().resolvedOptions().timeZone;
}
/** Compact age: "just now" / "45m ago" / "3h ago" / "2d ago", and from a week on the day itself in `zone` ("Aug 3", with
 *  the year when it is another year there). Same caveat as fmtDateTime — pass an instant, not a bare wall-clock string. */
export function fmtRelative(iso: string, zone: string | null, now: Date = clock.now()): string {
  const d = new Date(iso);
  const then = d.getTime();
  if (Number.isNaN(then)) return "unknown";
  const mins = Math.floor((now.getTime() - then) / 60_000);
  // a clock skew or a just-written timestamp can land slightly in the future;
  // "in -2m ago" helps nobody
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return zoneFormat(zone, otherYear(d, now, zone) ? "dayYear" : "day").format(d);
}
/** Hours since an ISO instant, or null if it's missing/unparseable. Drives the
 *  freshness tone (lib/refreshSchedule.ts reads it against the schedule's gaps). */
export function hoursSince(iso: string | null, now: Date = clock.now()): number | null {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  return (now.getTime() - then) / 3_600_000;
}
/** local-timezone YYYY-MM-DD (Date.toISOString shifts to UTC — don't use it for dates) */
export function localISO(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** "Jul 2026": a month in a card, a column head, a KPI or prose. The year is spelled out because "Jul 26" reads as a
 *  date (July 26th); an axis, where width is scarce, uses fmtMonthAxis. */
export function fmtMonth(ym: string): string {
  const [y, m] = ym.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("en-US", {
    month: "short", year: "numeric",
  });
}
/** "Jul ’26": a month on a chart axis, where "Jul 2026" is two characters too wide and a bare "Jul 26" reads as a
 *  day of the month - the apostrophe says it is a year. */
export function fmtMonthAxis(ym: string): string {
  const [y, m] = ym.split("-").map(Number);
  const month = new Date(y, m - 1, 1).toLocaleDateString("en-US", { month: "short" });
  return `${month} \u2019${String(y % 100).padStart(2, "0")}`;
}
