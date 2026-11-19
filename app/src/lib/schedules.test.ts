// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { acctFactory, recFactory } from "./__fixtures__";
import { knownThroughFor, recurringKey, scheduleStatus, statusChip, visibleRecurring, type ScheduleStatus } from "./schedules";

// monthly schedule: last paid 2026-06-05, next expected 2026-07-05, grace 6 -> deadline 2026-07-11
const rec = recFactory({ last_seen: "2026-06-05", expected_dates: ["2026-07-05", "2026-08-05", "2026-09-05"], grace_days: 6 });
const KNOWN = "2026-07-20";

describe("scheduleStatus", () => {
  it("inactive when not promoted (or no expected dates)", () => {
    expect(scheduleStatus(rec({ schedule: false }), "2026-07-20", KNOWN)).toBe("inactive");
    expect(scheduleStatus(rec({ expected_dates: [] }), "2026-07-20", KNOWN)).toBe("inactive");
  });
  it("paid beats everything: a charge within grace_days of today, even with a far-off expected date", () => {
    expect(scheduleStatus(rec(), "2026-06-11", KNOWN)).toBe("paid");                                 // 6 days: boundary
    expect(scheduleStatus(rec({ expected_dates: ["2026-12-01"] }), "2026-06-10", KNOWN)).toBe("paid");  // second clause
    expect(scheduleStatus(rec(), "2026-06-12", KNOWN)).not.toBe("paid");                              // 7 days
  });
  it("missed needs data through the deadline", () => {
    expect(scheduleStatus(rec(), "2026-07-15", "2026-07-14")).toBe("missed");
    expect(scheduleStatus(rec(), "2026-07-15", "2026-07-11")).toBe("missed");                        // knownThrough == deadline
    expect(scheduleStatus(rec(), "2026-07-12", "2026-07-11")).toBe("missed");                        // first day past the deadline
  });
  it("unknown (data stale) when the account's data stops before the deadline, or is missing", () => {
    expect(scheduleStatus(rec(), "2026-07-15", "2026-07-10")).toBe("unknown");                       // data stale: never "missed"
    expect(scheduleStatus(rec(), "2026-07-15", null)).toBe("unknown");
  });
  it("due from the day before the expected date through the grace window", () => {
    expect(scheduleStatus(rec(), "2026-07-04", KNOWN)).toBe("due");
    expect(scheduleStatus(rec(), "2026-07-05", KNOWN)).toBe("due");
    expect(scheduleStatus(rec(), "2026-07-08", KNOWN)).toBe("due");                                  // inside grace, no charge yet
    expect(scheduleStatus(rec(), "2026-07-11", KNOWN)).toBe("due");                                  // deadline day
  });
  it("upcoming within 14 days, scheduled beyond", () => {
    expect(scheduleStatus(rec(), "2026-07-03", KNOWN)).toBe("upcoming");
    expect(scheduleStatus(rec(), "2026-06-21", KNOWN)).toBe("upcoming");                             // exactly 14 days
    expect(scheduleStatus(rec(), "2026-06-20", KNOWN)).toBe("scheduled");                            // 15 days (and > grace since last_seen)
  });

  // `active` stays true for up to 2x the median gap, so on a promoted row
  // expected_dates[0] (sometimes [1]) can already sit BEFORE the account's data end. A past
  // expected date is not "missed" by itself - only the data decides.
  describe("a past expected_dates[0] (active rows live up to 2x the median gap)", () => {
    // last charge 2026-06-05, so expected[0] = 07-05 is already behind a 07-20 data end
    it("is due, not missed, while today is still inside the grace window", () => {
      expect(scheduleStatus(rec(), "2026-07-09", "2026-07-09")).toBe("due");
      expect(scheduleStatus(rec(), "2026-07-11", "2026-07-20")).toBe("due");           // data ahead of today's clock, still in grace
    });
    it("is data stale when the data end is past the expected date but short of the deadline", () => {
      expect(scheduleStatus(rec(), "2026-07-20", "2026-07-08")).toBe("unknown");       // expected 07-05 <= known 07-08 < deadline 07-11
      expect(scheduleStatus(rec(), "2026-07-20", "2026-07-05")).toBe("unknown");       // known == expected date: grace days still unseen
    });
    it("is missed once the data covers the deadline (the charge truly never posted)", () => {
      expect(scheduleStatus(rec(), "2026-07-20", KNOWN)).toBe("missed");
    });
    it("is paid when the account's data shows a charge within grace of today", () => {
      expect(scheduleStatus(rec({ last_seen: "2026-07-17" }), "2026-07-20", KNOWN)).toBe("paid");
    });
    it("judges the FIRST unpaid expected date even when expected_dates[1] is also behind the data", () => {
      const old = rec({ last_seen: "2026-04-05", expected_dates: ["2026-05-05", "2026-06-05", "2026-07-05"] });
      expect(scheduleStatus(old, "2026-07-20", KNOWN)).toBe("missed");
      expect(statusChip("missed", old)?.label).toBe("missed · expected May 5, 2026");
      expect(scheduleStatus(old, "2026-07-20", "2026-05-08")).toBe("unknown");         // account feed died in the grace window
    });
  });
});

describe("statusChip / helpers", () => {
  it("renders a chip for every judged status, scheduled (further off than upcoming) included, and none for inactive", () => {
    const r = rec();
    expect(statusChip("paid", r)?.label).toBe("✓ paid Jun 5, 2026");
    expect(statusChip("missed", r)?.label).toBe("missed · expected Jul 5, 2026");
    expect(statusChip("unknown", r)?.label).toBe("data stale");
    expect(statusChip("due", r)?.label).toBe("due · expected Jul 5, 2026");     // not "due Jul 5": inside the grace window that date is behind us
    expect(statusChip("upcoming", r)?.label).toBe("upcoming Jul 5, 2026");
    expect(statusChip("inactive", r)).toBeNull();
    expect(statusChip("scheduled", r)?.label, "a row past 14 days says which state it is in").toBe("scheduled Jul 5, 2026");
    expect(statusChip("scheduled", r)?.title).toBe("expected in more than 14 days");
    expect(statusChip("missed", r)?.title).toMatch(/data covers/);
  });
  it("knownThroughFor reads the owning account's txn_data_end, null when unknown", () => {
    const acct = acctFactory();
    expect(knownThroughFor([acct({ id: "a", txn_data_end: "2026-07-01" }), acct({ id: "b", txn_data_end: null })], "a")).toBe("2026-07-01");
    expect(knownThroughFor([acct({ id: "b", txn_data_end: null })], "b")).toBeNull();
    expect(knownThroughFor([], "zzz")).toBeNull();
  });
});

describe("visibleRecurring: the rows and the 'missed only' count come from one filter", () => {
  const TODAY = "2026-07-20";
  // Gym + Old are missed (data through the deadline); Paid is paid; Plain is not a schedule; Lapsed is an inactive non-schedule
  const gym = rec({ vendor: "Gym", monthly_equivalent: 45 });
  const old = rec({ vendor: "Old", last_seen: "2026-03-05", first_seen: "2025-03-05", monthly_equivalent: 90,
    expected_dates: ["2026-04-05", "2026-05-05", "2026-06-05"] });
  const paid = rec({ vendor: "Paid", last_seen: "2026-07-18", expected_dates: ["2026-08-18", "2026-09-18", "2026-10-18"], monthly_equivalent: 10 });
  const plain = rec({ vendor: "Plain", schedule: false, expected_dates: [], monthly_equivalent: 5 });
  const lapsed = rec({ vendor: "Lapsed", schedule: false, active: false, expected_dates: [], last_seen: "2025-01-01", monthly_equivalent: 1 });
  const all = [gym, old, paid, plain, lapsed];
  const status = new Map<string, ScheduleStatus>(all.map((r) => [recurringKey(r), scheduleStatus(r, TODAY, KNOWN)]));
  const base = { showInactive: false, explicit: false, missedOnly: false, status };
  const names = (o: Partial<Parameters<typeof visibleRecurring>[1]>) => visibleRecurring(all, { ...base, ...o }).items.map((r) => r.vendor);

  it("sanity: the fixtures have the statuses the cases below rely on", () => {
    expect(all.map((r) => status.get(recurringKey(r)))).toEqual(["missed", "missed", "paid", "inactive", "inactive"]);
  });
  it("counts every missed schedule and lists them largest first when nothing narrows the view", () => {
    expect(visibleRecurring(all, base).missedCount).toBe(2);
    expect(names({})).toEqual(["Old", "Gym", "Paid", "Plain"]);               // lapsed hidden, sorted by monthly equivalent
    expect(names({ missedOnly: true })).toEqual(["Old", "Gym"]);
  });
  it("the count follows the date window — a missed schedule outside it is neither counted nor listed", () => {
    const windowed = { explicit: true, from: "2026-06-01" };                   // Old's last charge (Mar) is before the window
    expect(visibleRecurring(all, { ...base, ...windowed }).missedCount).toBe(1);
    expect(names({ ...windowed, missedOnly: true })).toEqual(["Gym"]);
    const early = { explicit: true, to: "2025-02-01" };                        // Old started 2025-03-05, after the window ends
    expect(visibleRecurring(all, { ...base, ...early }).missedCount).toBe(1);
    expect(names({ ...early, missedOnly: true })).toEqual(["Gym"]);
  });
  it("pressing the pill always leaves exactly `missedCount` rows, whatever the other controls say", () => {
    for (const showInactive of [false, true]) {
      for (const win of [{}, { explicit: true, from: "2026-06-01" }, { explicit: true, to: "2025-12-31" }, { explicit: true, from: "2027-01-01" }]) {
        const o = { ...base, showInactive, ...win };
        const off = visibleRecurring(all, o);
        const on = visibleRecurring(all, { ...o, missedOnly: true });
        expect(on.items.length, JSON.stringify({ showInactive, win })).toBe(off.missedCount);
        expect(on.items.every((r) => status.get(recurringKey(r)) === "missed")).toBe(true);
        expect(on.missedCount).toBe(off.missedCount);
      }
    }
  });
  it("recurringKey separates vendor and account unambiguously", () => {
    expect(recurringKey({ vendor: "A", account_id: "BC" })).not.toBe(recurringKey({ vendor: "AB", account_id: "C" }));
  });
});
