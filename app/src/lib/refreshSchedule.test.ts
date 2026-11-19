// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { FALLBACK_GAP_H, freshness, type RefreshSchedule, scheduleMinutes, staleHours } from "./refreshSchedule";

const S = (times: string[], timezone = "America/Denver"): RefreshSchedule => ({ times, timezone });
const THREE = S(["01:00", "09:00", "17:00"]);

describe("the schedule's times", () => {
  it("are read as minutes after midnight, sorted, each once; anything that is not HH:MM is left out", () => {
    expect(scheduleMinutes(S(["17:00", "01:00", "09:00", "01:00"]))).toEqual([60, 540, 1020]);
    expect(scheduleMinutes(S(["25:00", "1:00", "ab", "23:59"]))).toEqual([1439]);
    expect(scheduleMinutes(S([]))).toBeNull();
    expect(scheduleMinutes(null)).toBeNull();
    expect(scheduleMinutes(undefined)).toBeNull();
  });
});

describe("the stale thresholds come from the schedule", () => {
  it("amber after one and a quarter of the longest gap between scheduled refreshes, red after three", () => {
    expect(staleHours(THREE)).toEqual({ amber: 10, red: 24, scheduled: true });
    // 06:00 and 07:00: the longest gap is the 23 hours from 07:00 round to 06:00
    expect(staleHours(S(["06:00", "07:00"]))).toEqual({ amber: 28.75, red: 69, scheduled: true });
    expect(staleHours(S(["06:00"])), "one a day: a gap of a day").toEqual({ amber: 30, red: 72, scheduled: true });
  });
  it("with no schedule in the status, the thresholds of a once-a-day schedule", () => {
    expect(FALLBACK_GAP_H).toBe(24);
    expect(staleHours(null)).toEqual({ amber: 30, red: 72, scheduled: false });
    expect(staleHours(S(["bad"]))).toEqual({ amber: 30, red: 72, scheduled: false });
  });
  it("the pill's tone and why, by the age of the last feed fetch", () => {
    expect(freshness(null, THREE)).toEqual({ tone: "never", hint: "The bank feed has not been fetched yet." });
    expect(freshness(9.9, THREE)).toEqual({ tone: "fresh", hint: "" });
    expect(freshness(10, THREE)).toEqual({ tone: "amber", hint: "Over 10 hours old: a scheduled refresh may have been missed." });
    expect(freshness(24, THREE)).toEqual({ tone: "red", hint: "Over a day old: the scheduled refresh may be failing." });
    expect(freshness(29.9, null).tone).toBe("fresh");
    expect(freshness(30, null)).toEqual({ tone: "amber", hint: "Over 30 hours old." });
    expect(freshness(72, null)).toEqual({ tone: "red", hint: "Over 3 days old: check that the bank feed is still being fetched." });
    // every half hour: the longest gap is 30 minutes
    const halfHourly = S(Array.from({ length: 48 }, (_, i) => `${String(Math.floor(i / 2)).padStart(2, "0")}:${i % 2 ? "30" : "00"}`));
    expect(freshness(0.7, halfHourly).hint).toBe("Over 37 minutes old: a scheduled refresh may have been missed.");
    expect(freshness(1.5, halfHourly).hint).toBe("Over an hour old: the scheduled refresh may be failing.");
  });
  it("the last rebuild of a household with no feed: toned only by a reported schedule, in words that name no feed", () => {
    expect(freshness(9, THREE, "rebuild")).toEqual({ tone: "fresh", hint: "" });
    expect(freshness(10, THREE, "rebuild")).toEqual({ tone: "amber", hint: "Rebuilt over 10 hours ago: a scheduled rebuild may have been missed." });
    expect(freshness(24 * 90, THREE, "rebuild")).toEqual({ tone: "red", hint: "Rebuilt over a day ago: the scheduled rebuild may be failing." });
    expect(freshness(24 * 90, null, "rebuild"), "no schedule: rebuilt by hand").toEqual({ tone: "fresh", hint: "" });
  });
});

// The schedule sentence and the next scheduled refresh are clock times: lib/clockTimes.test.ts, which the gate also
// runs in two other process zones.

// No sentence in the dashboard's code states a pull frequency as a fact of the product, or names a machine: the
// schedule is the status's and is read from it (lib/clockTimes.test.ts), the store is "the dashboard's store". Every non-test source
// file is read, comments included; the test files may use the words. A hit prints its file and line number only.
const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");
const TEST_SUPPORT = join(SRC, "test");
function appFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? (join(dir, e.name) !== TEST_SUPPORT ? appFiles(join(dir, e.name)) : [])
    : /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) && e.name !== "__fixtures__.ts" ? [join(dir, e.name)] : []);
}
const COUNT = String.raw`(?:\d+|one|two|three|four|five|six|eight|twelve|twenty-four)`;
const FREQUENCY = [
  /\b\d+\s*[×x]\s*(?:\/\s*day|-?\s*daily|a day|per day)/i,                      // "3×/day", "3×-daily"
  new RegExp(String.raw`\b${COUNT}\s+times\s+(?:(?:a|per|each)\s+day|daily)\b`, "i"),   // "three times a day", "3 times daily"
  /\b(?:once|twice|thrice)\s+(?:(?:a|per|each)\s+day|daily|nightly)\b/i,         // "twice daily"
  // "a nightly refresh", "pulled hourly" (a rental's "nightly rate" is a price, not a schedule)
  /\b(?:hourly|nightly)\s+(?:refresh|pull|fetch|ingest|rebuild|update|sync|run|job)/i,
  /\b(?:refresh|pull|fetch|ingest|rebuild|update|sync|run)\w*\s+(?:\w+\s+)?(?:hourly|nightly)\b/i,
  new RegExp(String.raw`\bevery\s+${COUNT}\s*(?:h|hrs?|hours?)\b`, "i"),          // "every 8 hours", "every eight hours"
  /\b\d+\s*(?:h|hours?)\s+apart\b/i,                                              // "8h apart"
  /(?<![:\d])\d{1,2}\s?(?:am|pm)\b/i,                                             // "1am, 9am and 5pm"
  /\b\d{2}:\d{2}\s*\/\s*\d{2}:\d{2}/,                                             // "01:00/09:00/17:00"
];
const MACHINE = /\b(?:home server|this box|this machine)\b/i;
describe("no sentence states a pull frequency or names a machine", () => {
  const files = appFiles(SRC);
  it("scans a non-trivial corpus, so an empty read cannot pass", () => {
    expect(files.length).toBeGreaterThan(50);
  });
  it("the patterns catch what they are for, and leave a schedule read from the data alone", () => {
    for (const s of ["runs 3×/day", "the 3×-daily pull", "three times a day", "twice a day", "every 8 hours",
      "8h apart", "at 1am, 9am and 5pm", "(01:00/09:00/17:00 CT)", "6 times a day", "3 times daily", "three times daily",
      "twice daily", "pulled hourly", "a nightly refresh", "every eight hours", "it runs nightly", "Hourly updates"]) {
      expect(FREQUENCY.some((re) => re.test(s)), s).toBe(true);
    }
    for (const s of ["save to the home server", "on this box", "stays on this machine"]) expect(MACHINE.test(s), s).toBe(true);
    for (const s of ["`${n} times a day, next at`", "automatically ${minutes.length} times a day, next at ${next}",
      "at the next refresh", "three times at a regular interval", "every day", "daily spending", "the hour",
      "Nightly rate", "breakeven not achievable at this nightly rate",
      "the table pans inside this wrapper", "17:00", "1:00 AM"]) {
      expect(FREQUENCY.some((re) => re.test(s)) || MACHINE.test(s), s).toBe(false);
    }
  });
  it("no source line does either", () => {
    const stray = files.flatMap((f) => readFileSync(f, "utf8").split("\n").flatMap((line, i) =>
      FREQUENCY.some((re) => re.test(line)) || MACHINE.test(line) ? [`${f.slice(SRC.length + 1)}:${i + 1}`] : []));
    expect(stray).toEqual([]);
  });
});
