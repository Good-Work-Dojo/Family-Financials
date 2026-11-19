// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Schedule status. The ETL ships clock-free facts — last_seen, the next expected dates and a
// grace window per cadence — and the APP judges them against today and the owning account's
// txn_data_end, the honest "data known through" date for feed and CSV accounts alike. "missed"
// is only ever claimed when the data actually covers the deadline; otherwise "data stale".
// `today` is the caller's: the household's day, `clock.today(household.timezone)` (lib/clock.ts).
import dayjs from "dayjs";
import type { AccountMeta, RecurringItem } from "../data/types";
import { fmtDate } from "./format";

export type ScheduleStatus = "inactive" | "paid" | "missed" | "unknown" | "due" | "upcoming" | "scheduled";

/** the owning account's "transactions known through" date; null when the account (or its date) is unknown */
export function knownThroughFor(accounts: readonly AccountMeta[], accountId: string): string | null {
  return accounts.find((a) => a.id === accountId)?.txn_data_end ?? null;
}

/** calendar days from a to b (b − a) */
const daysBetween = (a: string, b: string) => dayjs(b).diff(dayjs(a), "day");
const plusDays = (iso: string, n: number) => dayjs(iso).add(n, "day").format("YYYY-MM-DD");

/**
 * Judge one recurring row against `today` and the owning account's data horizon.
 *
 * `active` stays true for up to 2x the median gap, so on a promoted row `expected_dates[0]` (and
 * sometimes `[1]`) can already sit before the account's data end. A past expected date is therefore
 * never "missed" by itself: `paid` is checked first (a charge within grace of today), and `missed`
 * additionally needs the account's data to reach the grace deadline — otherwise it is `unknown`.
 */
export function scheduleStatus(item: RecurringItem, today: string, knownThrough: string | null): ScheduleStatus {
  if (!item.schedule || item.expected_dates.length === 0) return "inactive";
  if (daysBetween(item.last_seen, today) <= item.grace_days) return "paid";   // recently paid: the ETL derives expected_dates FROM last_seen
  const expected = item.expected_dates[0];
  const deadline = plusDays(expected, item.grace_days);
  if (today > deadline) return knownThrough !== null && knownThrough >= deadline ? "missed" : "unknown";
  if (today >= plusDays(expected, -1)) return "due";                          // the day before through the grace window
  if (daysBetween(today, expected) <= 14) return "upcoming";
  return "scheduled";
}

/** stable key for a recurring series (vendor + owning account); NUL cannot occur in either part */
export const recurringKey = (r: Pick<RecurringItem, "vendor" | "account_id">) => `${r.vendor}\u0000${r.account_id}`;

/**
 * The Recurring table's row set AND the "missed only" pill's count, from one filter so they cannot
 * disagree: `missedCount` is the number of missed schedules among the rows the table would show with
 * `missedOnly` off (lapsed toggle and date window applied), which is exactly what pressing the pill leaves.
 * Rows are sorted by monthly equivalent, largest first.
 */
export function visibleRecurring(recurring: readonly RecurringItem[], o: {
  showInactive: boolean;
  /** a date window is set in the URL — "was active then" is the overlap test itself */
  explicit: boolean;
  from?: string;
  to?: string;
  missedOnly: boolean;
  status: ReadonlyMap<string, ScheduleStatus>;
}): { items: RecurringItem[]; missedCount: number } {
  const inWindow = recurring.filter((r) =>
    (o.showInactive || r.active || o.explicit)
    && (!o.to || r.first_seen <= o.to)
    && (!o.from || r.last_seen >= o.from));
  const isMissed = (r: RecurringItem) => o.status.get(recurringKey(r)) === "missed";
  const shown = o.missedOnly ? inWindow.filter(isMissed) : inWindow;
  return {
    items: [...shown].sort((a, b) => b.monthly_equivalent - a.monthly_equivalent),
    missedCount: inWindow.filter(isMissed).length,
  };
}

export function statusChip(status: ScheduleStatus, item: RecurringItem): { label: string; title: string; cls: string } | null {
  switch (status) {
    case "paid": return { label: `✓ paid ${fmtDate(item.last_seen)}`, cls: "bg-emerald-950 text-emerald-300",
      title: "a charge posted within the grace window of today" };
    // "due · expected Oct 3", not "due Oct 3": inside the grace window the date is already behind us, and a bare date after
    // "due" reads as overdue; this is the same shape as the missed chip's "missed · expected …"
    case "due": return { label: `due · expected ${fmtDate(item.expected_dates[0])}`, cls: "bg-amber-950 text-amber-300",
      title: "expected now — from the day before through the grace window" };
    case "upcoming": return { label: `upcoming ${fmtDate(item.expected_dates[0])}`, cls: "bg-blue-950 text-blue-300",
      title: "expected within 14 days" };
    case "missed": return { label: `missed · expected ${fmtDate(item.expected_dates[0])}`, cls: "bg-red-950 text-red-300",
      title: "nothing posted within the grace window, and the account's data covers it — cancelled, moved, or a failed payment" };
    case "unknown": return { label: "data stale", cls: "bg-zinc-800 text-zinc-400",
      title: "the expected date passed, but this account's transactions do not reach the grace deadline yet — not a missed payment" };
    // further off than "upcoming": a state of its own, which a list of next charges names as such rather than leaving its
    // row unmarked beside the upcoming ones
    case "scheduled": return { label: `scheduled ${fmtDate(item.expected_dates[0])}`, cls: "bg-zinc-800 text-zinc-300",
      title: "expected in more than 14 days" };
    default: return null;
  }
}
