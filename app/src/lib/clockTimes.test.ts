// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { fmtDateTime, fmtRelative, fmtTime, zoneName } from "./format";
import { nextRefreshSentence, type RefreshSchedule, scheduleSentence } from "./refreshSchedule";

// Clock times are the household's. Every expectation here is a fixed text for a household in a zone of its own, so it
// must not move with the zone the test process runs in: the suite runs this file in UTC, and the gate runs it again
// with the route matrix under FF_TEST_TZ=America/New_York and Pacific/Kiritimati (tools/verify/ci.sh).

// the reader's 12- or 24-hour clock (the browser's locale): the expected text in either
const cycle = new Intl.DateTimeFormat(undefined, { hour: "numeric" }).resolvedOptions().hourCycle;
const t = (twelve: string, twentyFour: string) => (cycle === "h23" || cycle === "h24" ? twentyFour : twelve);

describe("fmtRelative", () => {
  const MT = "America/Denver";
  const NOW = new Date("2026-08-11T12:00:00Z");
  const ago = (mins: number) => new Date(NOW.getTime() - mins * 60_000).toISOString();
  it("steps from just now through days", () => {
    expect(fmtRelative(ago(0), MT, NOW)).toBe("just now");
    expect(fmtRelative(ago(45), MT, NOW)).toBe("45m ago");
    expect(fmtRelative(ago(59), MT, NOW)).toBe("59m ago");
    expect(fmtRelative(ago(60), MT, NOW)).toBe("1h ago");
    expect(fmtRelative(ago(3 * 60), MT, NOW)).toBe("3h ago");
    expect(fmtRelative(ago(48 * 60), MT, NOW)).toBe("2d ago");
    expect(fmtRelative(ago(7 * 24 * 60 - 1), MT, NOW)).toBe("6d ago");
  });
  it("an age of a week or more prints the day instead, in the household's zone, with the year when it is another year there", () => {
    expect(fmtRelative(ago(7 * 24 * 60), MT, NOW)).toBe("Aug 4");
    // 03:00Z on Aug 1 is Jul 31 in Denver and Aug 1 on Kiritimati, whatever zone the process runs in
    expect(fmtRelative("2026-08-01T03:00:00Z", MT, NOW)).toBe("Jul 31");
    expect(fmtRelative("2026-08-01T03:00:00Z", "Pacific/Kiritimati", NOW)).toBe("Aug 1");
    expect(fmtRelative("2025-12-01T18:00:00Z", MT, NOW)).toBe("Dec 1, 2025");
  });
  it("does not render a negative age from clock skew", () => {
    expect(fmtRelative(ago(-90), MT, NOW)).toBe("just now");
  });
  it("survives a malformed timestamp instead of rendering NaN", () => {
    expect(fmtRelative("not a date", MT, NOW)).toBe("unknown");
  });
  it("reads the Z suffix as UTC, not as local time", () => {
    // 09:00Z is 03:00 MDT; read as local time it would be 15:00Z, an hour in the future
    expect(fmtRelative("2026-08-11T09:00:00Z", MT, NOW)).toBe("3h ago");
  });
});

describe("fmtDateTime: in the household's zone, with its abbreviation, and the year when it is another year there", () => {
  const NOW = new Date("2026-08-11T12:00:00Z");
  it("prints the instant on the household's clock, whatever the process zone", () => {
    expect(fmtDateTime("2026-08-10T23:00:00Z", "America/Denver", NOW)).toBe(`Aug 10, ${t("5:00 PM", "17:00")} MDT`);
    expect(fmtDateTime("2026-08-10T22:00:00Z", "Asia/Kolkata", NOW)).toBe(`Aug 11, ${t("3:30 AM", "03:30")} GMT+5:30`);
    // generated_at carries the household's offset: the same instant, the same text
    expect(fmtDateTime("2026-08-10T17:00:00-06:00", "America/Denver", NOW)).toBe(`Aug 10, ${t("5:00 PM", "17:00")} MDT`);
    expect(fmtDateTime("2026-02-11T00:30:00Z", "America/Denver", NOW)).toBe(`Feb 10, ${t("5:30 PM", "17:30")} MST`);
  });
  it("adds the year whenever the instant is in another year than the household's current one", () => {
    // Jan 1 04:00Z is Dec 31 in Denver (another year) and Jan 1 on Kiritimati (this year)
    expect(fmtDateTime("2026-01-01T04:00:00Z", "America/Denver", NOW)).toBe(`Dec 31, 2025, ${t("9:00 PM", "21:00")} MST`);
    expect(fmtDateTime("2026-01-01T04:00:00Z", "Pacific/Kiritimati", NOW)).toBe(`Jan 1, ${t("6:00 PM", "18:00")} GMT+14`);
    // the current year is the household's too: at 03:00Z on Jan 1 it is still Dec 31 in Denver
    expect(fmtDateTime("2025-12-31T19:00:00Z", "America/Denver", new Date("2026-01-01T03:00:00Z")))
      .toBe(`Dec 31, ${t("12:00 PM", "12:00")} MST`);
    expect(fmtDateTime("2027-03-01T19:00:00Z", "America/Denver", NOW)).toBe(`Mar 1, 2027, ${t("12:00 PM", "12:00")} MST`);
  });
  it("with no zone, or one this browser's Intl refuses, it is the browser's own zone; never throws", () => {
    const own = Intl.DateTimeFormat().resolvedOptions().timeZone;
    expect(fmtDateTime("2026-08-10T22:00:00Z", null, NOW)).toBe(fmtDateTime("2026-08-10T22:00:00Z", own, NOW));
    expect(fmtDateTime("2026-08-10T22:00:00Z", "Not/A_Zone", NOW)).toBe(fmtDateTime("2026-08-10T22:00:00Z", null, NOW));
  });
  it("survives a malformed timestamp", () => {
    expect(fmtDateTime("not a date", "America/Denver", NOW)).toBe("unknown");
  });
  it("a clock time alone follows the reader's 12- or 24-hour convention, in the zone given", () => {
    expect(fmtTime(new Date("2026-08-10T07:00:00Z"), "America/Denver")).toBe(t("1:00 AM", "01:00"));
    expect(fmtTime(new Date("2026-08-10T22:05:00Z"), "UTC")).toBe(t("10:05 PM", "22:05"));
  });
  it("a zone's name, from Intl: its generic name, else its long one", () => {
    expect(zoneName("America/Denver", NOW)).toBe("Mountain Time");
    expect(zoneName("America/New_York", NOW)).toBe("Eastern Time");
    expect(zoneName("UTC", NOW)).toBe("Coordinated Universal Time");
    expect(zoneName(null, NOW)).toBe(zoneName(Intl.DateTimeFormat().resolvedOptions().timeZone, NOW));
  });
});

describe("the schedule sentence: the household's own clock, in its zone, from the status", () => {
  const MT = "America/Denver";
  const S = (times: string[], timezone = MT): RefreshSchedule => ({ times, timezone });
  const THREE = S(["01:00", "09:00", "17:00"]);
  /** 10:00 in Denver (MDT, UTC−6) */
  const NOW = new Date("2026-08-11T16:00:00Z");
  it("one, two, three or four times are listed", () => {
    expect(scheduleSentence(S(["06:00"]), MT, NOW)).toBe(`Refreshes automatically at ${t("6:00 AM", "06:00")} (Mountain Time).`);
    expect(scheduleSentence(S(["13:00", "01:00"]), MT, NOW))
      .toBe(`Refreshes automatically at ${t("1:00 AM", "01:00")} and ${t("1:00 PM", "13:00")} (Mountain Time).`);
    expect(scheduleSentence(THREE, MT, NOW))
      .toBe(`Refreshes automatically at ${t("1:00 AM", "01:00")}, ${t("9:00 AM", "09:00")} and ${t("5:00 PM", "17:00")} (Mountain Time).`);
    expect(scheduleSentence(S(["00:00", "06:00", "12:00", "18:00"]), MT, NOW)).toBe("Refreshes automatically at "
      + `${t("12:00 AM", "00:00")}, ${t("6:00 AM", "06:00")}, ${t("12:00 PM", "12:00")} and ${t("6:00 PM", "18:00")} (Mountain Time).`);
  });
  it("more than four say how many, and when the next is", () => {
    const six = S(["00:00", "04:00", "08:00", "12:00", "16:00", "20:00"]);
    expect(scheduleSentence(six, MT, NOW)).toBe(`Refreshes automatically 6 times a day, next at ${t("12:00 PM", "12:00")} (Mountain Time).`);
    // 21:00 in Denver: the next is at midnight, tomorrow there
    expect(scheduleSentence(six, MT, new Date("2026-08-12T03:00:00Z")))
      .toBe(`Refreshes automatically 6 times a day, next at ${t("12:00 AM", "00:00")} tomorrow (Mountain Time).`);
  });
  it("a schedule kept in another zone is said in the household's", () => {
    // the store's clock in UTC: 07:00, 15:00 and 23:00 UTC are 1 AM, 9 AM and 5 PM in Denver in August
    expect(scheduleSentence(S(["07:00", "15:00", "23:00"], "UTC"), MT, NOW)).toBe(scheduleSentence(THREE, MT, NOW));
    // Denver's 1:00, 9:00 and 17:00 on Kiritimati (UTC+14), in the order its own clock reads them
    expect(scheduleSentence(THREE, "Pacific/Kiritimati", NOW)).toBe("Refreshes automatically at "
      + `${t("5:00 AM", "05:00")}, ${t("1:00 PM", "13:00")} and ${t("9:00 PM", "21:00")} (Line Islands Time).`);
    const six = S(["00:00", "04:00", "08:00", "12:00", "16:00", "20:00"]);
    // 10:00 in Denver is 06:00 tomorrow on Kiritimati; Denver's 12:00 is 08:00 there
    expect(scheduleSentence(six, "Pacific/Kiritimati", NOW))
      .toBe(`Refreshes automatically 6 times a day, next at ${t("8:00 AM", "08:00")} (Line Islands Time).`);
  });
  it("with no household zone it is the browser's own", () => {
    const own = Intl.DateTimeFormat().resolvedOptions().timeZone;
    expect(scheduleSentence(THREE, null, NOW)).toBe(scheduleSentence(THREE, own, NOW));
  });
  it("a household with no bank feed is told what the scheduled refresh does for it", () => {
    expect(scheduleSentence(S(["06:00"]), MT, NOW, "Rebuilds")).toBe(`Rebuilds automatically at ${t("6:00 AM", "06:00")} (Mountain Time).`);
  });
  it("no sentence without a schedule, with no time it can read, or in a zone this browser does not know", () => {
    expect(scheduleSentence(null, MT, NOW)).toBeNull();
    expect(scheduleSentence(S([]), MT, NOW)).toBeNull();
    expect(scheduleSentence(S(["01:00"], "Not/A_Zone"), MT, NOW)).toBeNull();
  });
  it("the next scheduled refresh, for the page that waits for the first data", () => {
    expect(nextRefreshSentence(THREE, MT, NOW)).toBe(`The next scheduled refresh is at ${t("5:00 PM", "17:00")} (Mountain Time).`);
    expect(nextRefreshSentence(THREE, MT, new Date("2026-08-12T00:00:00Z")))
      .toBe(`The next scheduled refresh is at ${t("1:00 AM", "01:00")} tomorrow (Mountain Time).`);
    expect(nextRefreshSentence(null, MT, NOW)).toBeNull();
  });
});
