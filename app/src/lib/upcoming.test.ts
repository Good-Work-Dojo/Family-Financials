// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { acctFactory, recFactory } from "./__fixtures__";
import { upcomingSchedules } from "./upcoming";

// monthly schedule fixture: last paid 06-05, expected 07-05, grace 6 (deadline 07-11)
const rec = recFactory();
const acct = acctFactory();
const TODAY = "2026-07-01";
const accounts = [acct({ id: "demo_checking_0101", txn_data_end: "2026-07-20" }), acct({ id: "stale", txn_data_end: "2026-05-01" })];

describe("upcomingSchedules", () => {
  it("keeps due / upcoming / scheduled rows inside the horizon and drops paid, inactive and far-off ones", () => {
    const rows = upcomingSchedules([
      rec({ vendor: "Upcoming" }),                                                       // 07-05: upcoming (4 days)
      rec({ vendor: "Soon", expected_dates: ["2026-07-28", "2026-08-28", "2026-09-28"] }),   // 27 days: scheduled, inside 30
      rec({ vendor: "Far", expected_dates: ["2026-08-05", "2026-09-05", "2026-10-05"] }),    // 35 days: out
      rec({ vendor: "Paid", last_seen: "2026-06-28", expected_dates: ["2026-07-28"] }),      // paid within grace: out
      rec({ vendor: "Plain", schedule: false, expected_dates: [] }),                        // not promoted: out
    ], accounts, TODAY);
    expect(rows.map((r) => `${r.item.vendor}:${r.status}`)).toEqual(["Upcoming:upcoming", "Soon:scheduled"]);
  });
  it("puts missed first, then data-stale, then due, and carries the expected date", () => {
    const rows = upcomingSchedules([
      rec({ vendor: "Due", expected_dates: ["2026-07-01", "2026-08-01", "2026-09-01"] }),
      rec({ vendor: "Stale", account_id: "stale", last_seen: "2026-04-05", expected_dates: ["2026-05-05", "2026-06-05", "2026-07-05"] }),
      rec({ vendor: "Missed", last_seen: "2026-05-05", expected_dates: ["2026-06-05", "2026-07-05", "2026-08-05"] }),
    ], accounts, TODAY);
    expect(rows.map((r) => r.status)).toEqual(["missed", "unknown", "due"]);
    expect(rows[0].expected).toBe("2026-06-05");
  });
  it("the horizon is inclusive: today + 30 is in, today + 31 is out", () => {
    const rows = upcomingSchedules([
      rec({ vendor: "Edge in", expected_dates: ["2026-07-31", "2026-08-31", "2026-09-30"] }),    // exactly 30 days
      rec({ vendor: "Edge out", expected_dates: ["2026-08-01", "2026-09-01", "2026-10-01"] }),   // 31 days
    ], accounts, TODAY);
    expect(rows.map((r) => r.item.vendor)).toEqual(["Edge in"]);
    // the same boundary moves with `days`
    expect(upcomingSchedules([rec({ vendor: "Edge out", expected_dates: ["2026-08-01"] })], accounts, TODAY, 31)).toHaveLength(1);
    expect(upcomingSchedules([rec({ vendor: "Edge in", expected_dates: ["2026-07-31"] })], accounts, TODAY, 29)).toHaveLength(0);
  });
  it("orders rows of the same rank by expected date, whatever order they arrive in", () => {
    const rows = upcomingSchedules([
      rec({ vendor: "Scheduled B", expected_dates: ["2026-07-28"] }),   // 27 days: scheduled
      rec({ vendor: "Scheduled A", expected_dates: ["2026-07-20"] }),   // 19 days: scheduled
      rec({ vendor: "Upcoming B", expected_dates: ["2026-07-10"] }),    // 9 days: upcoming
      rec({ vendor: "Upcoming A", expected_dates: ["2026-07-04"] }),    // 3 days: upcoming
    ], accounts, TODAY);
    expect(rows.map((r) => `${r.item.vendor}:${r.status}`)).toEqual(
      ["Upcoming A:upcoming", "Upcoming B:upcoming", "Scheduled A:scheduled", "Scheduled B:scheduled"]);
    // missed rows too: the earlier missed date first
    const missed = upcomingSchedules([
      rec({ vendor: "Missed B", last_seen: "2026-05-20", expected_dates: ["2026-06-20"] }),
      rec({ vendor: "Missed A", last_seen: "2026-05-05", expected_dates: ["2026-06-05"] }),
    ], accounts, TODAY);
    expect(missed.map((r) => r.item.vendor)).toEqual(["Missed A", "Missed B"]);
  });
  it("skips a promoted row that carries no expected date", () => {
    expect(upcomingSchedules([rec({ vendor: "No dates", expected_dates: [] })], accounts, TODAY)).toHaveLength(0);
  });
  it("with no accounts.json every overdue row is data-stale, never missed", () => {
    const rows = upcomingSchedules([rec({ vendor: "Old", last_seen: "2026-05-05", expected_dates: ["2026-06-05"] })], [], TODAY);
    expect(rows[0].status).toBe("unknown");
  });
  it("honors a custom horizon", () => {
    expect(upcomingSchedules([rec()], accounts, TODAY, 2)).toHaveLength(0);
  });
});
