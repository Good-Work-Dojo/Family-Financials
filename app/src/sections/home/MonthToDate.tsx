// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useMemo } from "react";
import { Link } from "react-router-dom";
import { NO_TRANSACTIONS } from "../../components/NoData";
import { Card, PHONE_WRAP } from "../../components/ui";
import { useData } from "../../data/DataContext";
import { spendingPath } from "../../filters/filters";
import { coverageFloor, coverageFloorLabel, COVERED_FROM_DAY, monthInProgress, RUN_RATE_MIN_MONTHS } from "../../lib/agg";
import { countOf, fmtDate, fmtMonth, fmtUSD0 } from "../../lib/format";
import { lastCompleteDay, monthToDate, MTD_PRIOR_MONTHS, ordinal, paceSentence } from "../../lib/monthToDate";

const SECTION = "mb-1 text-[11px] font-semibold uppercase tracking-wide text-zinc-500";
const LINK = "inline-flex min-h-[40px] items-center text-blue-400 hover:underline md:min-h-0";

const PACE_TONE = { about: "text-zinc-200", above: "text-amber-300", below: "text-emerald-400" } as const;

/** Home: spending so far this calendar month, against the typical pace by the same day (lib/monthToDate.ts).
 *  Read-only: no budget to set, nothing to file. */
export default function MonthToDate() {
  const { data, indexes } = useData();
  // the data's last day (never past the ingest day: a pending charge's expected settlement is not data), and the last
  // COMPLETE day: the ingest day itself is still coming in, so the month is cut the day before (lib/monthToDate.ts).
  // Both are null with no transaction.
  const end = indexes.dataThrough;
  const through = lastCompleteDay(end, data.manifest.generated_at);
  // "this month" is the month in progress (lib/agg.ts monthInProgress), never a month a row was dated ahead into; it has
  // begun once its last complete day is in it (on the 1st, with the 1st still coming in, it has not)
  const inProgress = useMemo(() => monthInProgress(data), [data]);
  const started = inProgress !== null && through !== null && through.slice(0, 7) === inProgress;
  const floor = coverageFloor(data.cashflow);
  const floorLabel = coverageFloorLabel(data.cashflow);
  const r = useMemo(() => (started && through !== null ? monthToDate(data.transactions, through, floor) : null),
    [started, data.transactions, through, floor]);
  if (!r) {
    return (
      <Card testId="card-month-to-date" title="This month so far">
        <p data-testid="mtd-empty" className="text-xs text-zinc-500">
          {end !== null && inProgress !== null
            ? `No complete day of ${fmtMonth(inProgress)} yet: today, ${fmtDate(end)}, is still coming in.`
            : NO_TRANSACTIONS}
        </p>
      </Card>
    );
  }
  const first = `${r.month}-01`;
  const newest = r.priors[0]?.month, oldest = r.priors[r.priors.length - 1]?.month;
  const span = newest === oldest ? fmtMonth(newest ?? r.month) : `${fmtMonth(oldest!)} – ${fmtMonth(newest!)}`;
  return (
    <Card testId="card-month-to-date" title="This month so far"
      right={<span className="text-xs text-zinc-500">through {fmtDate(r.through)}</span>}>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="min-w-0">
          <div className="text-2xl font-semibold tabular-nums text-zinc-100" data-testid="mtd-spend">{fmtUSD0(r.spend)}</div>
          {/* its basis, on its own line: through the last complete day, which leaves out the day of the last ingest
              (still coming in) that the spending pages' totals count */}
          <div data-testid="mtd-basis" className="text-xs text-zinc-500">
            spent in {fmtMonth(r.month)} through {fmtDate(r.through)}
            {end !== null && end > r.through && `, the last complete day: ${fmtDate(end)} is still coming in`}
          </div>
          <p data-testid="mtd-pace" className={`mt-2 text-sm font-medium ${r.pace ? PACE_TONE[r.pace] : "text-zinc-300"}`}>
            {paceSentence(r)}
          </p>
          {r.typical !== null && (
            <p className="mt-0.5 text-xs text-zinc-500">
              typical by the {ordinal(r.day)}: {fmtUSD0(r.typical)} — the median of {countOf(r.priors.length, "month")} ({span})
            </p>
          )}
          {r.early && (
            <p data-testid="mtd-early" className="mt-1.5 text-[11px] text-amber-300/90">
              Early in the month: {r.day === 1 ? "one day" : countOf(r.day, "day")} of spending, so one bill landing a day sooner or later
              swings the comparison.
            </p>
          )}
        </div>
        <div className="min-w-0">
          <div className={SECTION}>Top categories so far</div>
          {r.top.length === 0 ? (
            <p className="text-xs text-zinc-500">No spending yet this month.</p>
          ) : (
            <ul data-testid="mtd-top" className="divide-y divide-zinc-800/60 text-xs">
              {r.top.map((c) => (
                <li key={c.category} className="flex items-center justify-between gap-2 py-1.5">
                  <span className={`min-w-0 text-zinc-200 ${PHONE_WRAP}`}>{c.category}</span>
                  <span className="shrink-0 tabular-nums">{fmtUSD0(c.spend)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 text-xs">
        <Link to={spendingPath({ from: first, to: r.through })} className={LINK}>This month’s transactions →</Link>
        <Link to="/cash-flow/budget" className={LINK}>Budget →</Link>
      </div>
      <p className="mt-1 text-[10px] text-faint">
        Through the last complete day: the day of the last ingest may still be coming in, so it waits for the next one.
        Spending = purchases and fees with refunds netted (transfers and card payments are not spending). Typical = the median
        of each of the last {MTD_PRIOR_MONTHS} complete months’ spending from the 1st through the same day (the whole month
        when it is shorter), once there are at least {RUN_RATE_MIN_MONTHS}; a month {floorLabel === null ? "" : `before ${floorLabel} or `}with
        no data is left out, and so is the data’s first month unless the data begins by its {ordinal(COVERED_FROM_DAY)}.
      </p>
    </Card>
  );
}
