// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Home's "next 30 days": which promoted schedules are about to hit (or should already have).
// Pure over lib/schedules.ts — the ONE status implementation — with `today` injected by Home.
import dayjs from "dayjs";
import type { AccountMeta, RecurringItem } from "../data/types";
import { knownThroughFor, scheduleStatus, type ScheduleStatus } from "./schedules";

export interface UpcomingRow { item: RecurringItem; status: ScheduleStatus; expected: string }

// paid = just posted (its next date is a cycle away); inactive = not a schedule
const SHOWN: ReadonlySet<ScheduleStatus> = new Set(["missed", "unknown", "due", "upcoming", "scheduled"]);
const RANK: Record<ScheduleStatus, number> = { missed: 0, unknown: 1, due: 2, upcoming: 3, scheduled: 3, paid: 9, inactive: 9 };

/** promoted schedules whose first unpaid expected date is within `days` of `today` (or already
 *  behind it), judged by scheduleStatus against each account's own data horizon */
export function upcomingSchedules(recurring: readonly RecurringItem[], accounts: readonly AccountMeta[],
                                  today: string, days = 30): UpcomingRow[] {
  const horizon = dayjs(today).add(days, "day").format("YYYY-MM-DD");
  const out: UpcomingRow[] = [];
  for (const item of recurring) {
    // no next date = nothing to list (scheduleStatus says "inactive" too; this does not lean on it)
    if (item.expected_dates.length === 0) continue;
    const status = scheduleStatus(item, today, knownThroughFor(accounts, item.account_id));
    if (!SHOWN.has(status)) continue;
    const expected = item.expected_dates[0];
    if (expected > horizon) continue;
    out.push({ item, status, expected });
  }
  return out.sort((a, b) => RANK[a.status] - RANK[b.status] || a.expected.localeCompare(b.expected));
}
