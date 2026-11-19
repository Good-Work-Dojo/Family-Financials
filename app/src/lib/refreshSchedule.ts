// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { dayOf } from "./clock";
import { fmtTime, zoneName } from "./format";

// The scheduled refresh as the status reports it (`GET /api/refresh` `schedule`: the crontab's times, `HH:MM`, in the
// store's zone; null when the store reports none) and what the dashboard says from it: the sentence that names the
// times, the next one, and how old the bank data may grow before the freshness pill turns amber or red. Nothing here
// knows a schedule of its own: with none reported there is no sentence, and the thresholds are a once-a-day schedule's.
// Every time is printed on the household's clock (`zone`, `assumptions.household.timezone`; null is the browser's).

export interface RefreshSchedule { times: string[]; timezone: string }

const HHMM = /^([01]\d|2[0-3]):([0-5]\d)$/;

/** the schedule's times as minutes after midnight, sorted, each once; null when it holds none that reads as HH:MM */
export function scheduleMinutes(s: RefreshSchedule | null | undefined): number[] | null {
  if (!s || !Array.isArray(s.times)) return null;
  const minutes = [...new Set(s.times.flatMap((t) => {
    const m = typeof t === "string" ? HHMM.exec(t) : null;
    return m ? [Number(m[1]) * 60 + Number(m[2])] : [];
  }))].sort((a, b) => a - b);
  return minutes.length > 0 ? minutes : null;
}

/** The longest gap the thresholds assume when the status reports no schedule: a once-a-day schedule's, the slowest that
 *  still brings the balances every day, so the pill warns no sooner than any real schedule would make it. */
export const FALLBACK_GAP_H = 24;

/** How old (hours) the bank data may be before the pill turns amber — one and a quarter of the longest gap between
 *  scheduled refreshes: one was missed — and red — three of them: the schedule may be failing. `scheduled` false: the
 *  status reports no schedule, and FALLBACK_GAP_H stands in for the gap. */
export function staleHours(s: RefreshSchedule | null | undefined): { amber: number; red: number; scheduled: boolean } {
  const m = scheduleMinutes(s);
  const gapH = m === null ? FALLBACK_GAP_H
    : Math.max(...m.map((x, i) => (i + 1 < m.length ? m[i + 1] : m[0] + 24 * 60) - x)) / 60;
  return { amber: 1.25 * gapH, red: 3 * gapH, scheduled: m !== null };
}

/** an age threshold in words: "37 minutes", "10 hours", "an hour", "a day", "3 days" (whole units, rounded down, so
 *  "over" stays true) */
function ageWords(h: number): string {
  if (h < 1) return `${Math.floor(h * 60)} minutes`;
  if (h >= 24 && h % 24 === 0) return h === 24 ? "a day" : `${h / 24} days`;
  const whole = Math.floor(h);
  return whole === 1 ? "an hour" : `${whole} hours`;
}

export type FreshnessTone = "fresh" | "amber" | "red" | "never";

/** The freshness pill's tone by the age (hours) of the last feed fetch, and why in a sentence: `never` for a feed never
 *  fetched (null), red and amber past the schedule's thresholds (staleHours). `what` "rebuild": the age of the last
 *  rebuild of a household with no feed, toned only when the status reports a schedule (a household whose rebuilds are
 *  run by hand has no schedule to fall behind), in words that name no feed. */
export function freshness(hours: number | null, s: RefreshSchedule | null | undefined,
  what: "feed" | "rebuild" = "feed"): { tone: FreshnessTone; hint: string } {
  const t = staleHours(s);
  if (what === "rebuild") {
    if (hours === null || !t.scheduled) return { tone: "fresh", hint: "" };
    if (hours >= t.red) return { tone: "red", hint: `Rebuilt over ${ageWords(t.red)} ago: the scheduled rebuild may be failing.` };
    if (hours >= t.amber) {
      return { tone: "amber", hint: `Rebuilt over ${ageWords(t.amber)} ago: a scheduled rebuild may have been missed.` };
    }
    return { tone: "fresh", hint: "" };
  }
  if (hours === null) return { tone: "never", hint: "The bank feed has not been fetched yet." };
  if (hours >= t.red) {
    return { tone: "red", hint: t.scheduled ? `Over ${ageWords(t.red)} old: the scheduled refresh may be failing.`
      : `Over ${ageWords(t.red)} old: check that the bank feed is still being fetched.` };
  }
  if (hours >= t.amber) {
    return { tone: "amber", hint: t.scheduled ? `Over ${ageWords(t.amber)} old: a scheduled refresh may have been missed.`
      : `Over ${ageWords(t.amber)} old.` };
  }
  return { tone: "fresh", hint: "" };
}

// ------------------------------------------------------------------------------------------------ zone arithmetic

/** a zone this browser's Intl knows */
function knownZone(zone: string): boolean {
  try {
    return new Intl.DateTimeFormat("en-US", { timeZone: zone }).resolvedOptions().timeZone !== "";
  } catch {
    return false;
  }
}
const partFormats = new Map<string, Intl.DateTimeFormat>();
/** the zone's offset from UTC (ms) at an instant */
function offsetMs(zone: string, at: number): number {
  let f = partFormats.get(zone);
  if (f === undefined) {
    f = new Intl.DateTimeFormat("en-US", { timeZone: zone, hourCycle: "h23", year: "numeric", month: "numeric",
      day: "numeric", hour: "numeric", minute: "numeric", second: "numeric" });
    partFormats.set(zone, f);
  }
  const parts = f.formatToParts(new Date(at));
  const n = (type: Intl.DateTimeFormatPartTypes) => Number(parts.find((p) => p.type === type)?.value ?? 0);
  return Date.UTC(n("year"), n("month") - 1, n("day"), n("hour") % 24, n("minute"), n("second")) - Math.floor(at / 1000) * 1000;
}
/** the instant a wall-clock time (minutes after midnight) of a day (`YYYY-MM-DD`) reads in `zone` */
function instantOf(day: string, minutes: number, zone: string): number {
  const [y, m, d] = day.split("-").map(Number);
  const wall = Date.UTC(y, m - 1, d, Math.floor(minutes / 60), minutes % 60);
  const first = offsetMs(zone, wall);
  const second = offsetMs(zone, wall - first);
  return wall - (second === first ? first : second);   // across a change of offset, the offset in force then
}
const addDays = (day: string, n: number): string => {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
};
/** minutes after midnight of an instant on the clock of `zone` (null: the browser's) */
function wallMinutes(at: number, zone: string | null): number {
  const parts = new Intl.DateTimeFormat("en-US", { hourCycle: "h23", hour: "numeric", minute: "numeric",
    ...(zone === null ? {} : { timeZone: zone }) }).formatToParts(new Date(at));
  const n = (type: Intl.DateTimeFormatPartTypes) => Number(parts.find((p) => p.type === type)?.value ?? 0);
  return (n("hour") % 24) * 60 + n("minute");
}

/** the household's zone as Intl will read it (a zone it refuses is the browser's, as lib/format.ts prints it) */
const readZone = (zone: string | null): string | null => (zone !== null && knownZone(zone) ? zone : null);
const ownZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone;

/** the schedule's times as the household reads them, in day order on its clock */
function timesIn(s: RefreshSchedule, minutes: number[], zone: string | null, now: Date): string[] {
  if (s.timezone === (zone ?? ownZone())) {
    return minutes.map((m) => fmtTime(new Date(Date.UTC(2000, 0, 1, Math.floor(m / 60), m % 60)), "UTC"));
  }
  const today = dayOf(now, s.timezone);
  return minutes.map((m) => instantOf(today, m, s.timezone))
    .map((at) => ({ at, wall: wallMinutes(at, zone) }))
    .sort((a, b) => a.wall - b.wall)
    .map(({ at }) => fmtTime(new Date(at), zone));
}

/** the next scheduled refresh after `now` and whether it falls tomorrow on the household's clock, in words */
function nextIn(s: RefreshSchedule, minutes: number[], zone: string | null, now: Date): string {
  const today = dayOf(now, s.timezone);
  const next = Math.min(...[-1, 0, 1, 2].flatMap((n) => minutes.map((m) => instantOf(addDays(today, n), m, s.timezone)))
    .filter((at) => at > now.getTime()));
  const tomorrow = dayOf(new Date(next), zone) !== dayOf(now, zone);
  return `${fmtTime(new Date(next), zone)}${tomorrow ? " tomorrow" : ""}`;
}

/** a list in words: "A", "A and B", "A, B and C" */
const listWords = (xs: string[]): string => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);

/** The schedule in a sentence on the household's clock: "Refreshes automatically at 1:00 AM, 9:00 AM and 5:00 PM
 *  (Central Time)." for up to four times, "Refreshes automatically N times a day, next at 12:00 PM (Central Time)." for
 *  more; `verb` "Rebuilds" for a household with no bank feed, whose scheduled refresh rebuilds from its files. null
 *  without a schedule, without a time it can read, or in a zone this browser does not know: no sentence rather than a
 *  wrong one. */
export function scheduleSentence(s: RefreshSchedule | null | undefined, zone: string | null, now: Date,
  verb: "Refreshes" | "Rebuilds" = "Refreshes"): string | null {
  const minutes = scheduleMinutes(s);
  if (!s || minutes === null || !knownZone(s.timezone)) return null;
  const z = readZone(zone);
  const label = zoneName(z, now);
  if (minutes.length > 4) return `${verb} automatically ${minutes.length} times a day, next at ${nextIn(s, minutes, z, now)} (${label}).`;
  return `${verb} automatically at ${listWords(timesIn(s, minutes, z, now))} (${label}).`;
}

/** "The next scheduled refresh is at 5:00 PM (Central Time).", "… at 1:00 AM tomorrow (…)": for the page that waits for
 *  a household's first data; null as scheduleSentence is */
export function nextRefreshSentence(s: RefreshSchedule | null | undefined, zone: string | null, now: Date): string | null {
  const minutes = scheduleMinutes(s);
  if (!s || minutes === null || !knownZone(s.timezone)) return null;
  const z = readZone(zone);
  return `The next scheduled refresh is at ${nextIn(s, minutes, z, now)} (${zoneName(z, now)}).`;
}
