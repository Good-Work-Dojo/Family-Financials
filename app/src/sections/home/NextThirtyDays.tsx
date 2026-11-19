// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useMemo } from "react";
import { Link } from "react-router-dom";
import RequireData from "../../components/RequireData";
import { Badge, Card, PHONE_WRAP } from "../../components/ui";
import { useData } from "../../data/DataContext";
import { fmtDate, fmtUSD0, wholeDollars } from "../../lib/format";
import { recurringKey, statusChip } from "../../lib/schedules";
import { upcomingSchedules } from "../../lib/upcoming";
import { useIsMobile } from "../../lib/useMediaQuery";

/** rows listed: twelve on a desktop; five below `md`, where eleven rows were a whole screen of Home - the rest is "N more →" */
const ROW_CAP = 12;
const PHONE_ROW_CAP = 5;

/** Home: the promoted schedules due in the next 30 days. `today` is injected by Home. */
export default function NextThirtyDays({ today }: { today: string }) {
  return (
    <RequireData files={["recurring.json"]} what="The next 30 days">
      <Body today={today} />
    </RequireData>
  );
}

function Body({ today }: { today: string }) {
  const { data } = useData();
  const rows = useMemo(() => upcomingSchedules(data.recurring, data.accounts, today),
    [data.recurring, data.accounts, today]);
  const cap = useIsMobile() ? PHONE_ROW_CAP : ROW_CAP;
  const listed = rows.slice(0, cap);
  const hidden = rows.length - listed.length;
  // the "≈" total is the sum of the rows listed, each as the card prints it (whole dollars), so it adds up to what the
  // eye adds up; the rows past the cap are named as not in it
  const total = listed.reduce((s, r) => s + wholeDollars(r.item.typical_amount), 0);
  return (
    <Card testId="card-next-30-days" title="Next 30 days — scheduled charges"
      right={<Link to="/spending/recurring" className="text-xs text-blue-400 hover:underline">all schedules →</Link>}>
      {rows.length === 0 ? (
        <p data-testid="next-30-empty" className="text-xs text-zinc-500">
          {/* every series carries its next expected dates; only one that cleared the promotion bar is a schedule */}
          {data.recurring.some((r) => r.schedule)
            ? "No scheduled charge is expected in the next 30 days."
            : "No schedule detected yet: a bill or subscription becomes one once the transactions show it repeating at a regular interval."}
        </p>
      ) : (
        <ul className="divide-y divide-zinc-800/60 text-xs">
          {listed.map(({ item, status, expected }) => {
            const chip = statusChip(status, item);
            return (
              <li key={recurringKey(item)} className="flex items-center justify-between gap-2 py-1.5">
                <span className={`min-w-0 ${PHONE_WRAP}`}>
                  <span className="font-medium text-zinc-200">{item.vendor}</span>
                  <span className="ml-1 text-faint">{item.cadence}</span>
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  {chip ? <Badge cls={chip.cls} title={chip.title}>{chip.label}</Badge>
                        : <span className="text-zinc-500">{fmtDate(expected)}</span>}
                  <span className="w-20 text-right tabular-nums">{fmtUSD0(item.typical_amount)}</span>
                </span>
              </li>
            );
          })}
        </ul>
      )}
      {hidden > 0 && (
        <p className="mt-1.5 text-xs">
          <Link to="/spending/recurring" className="inline-flex min-h-[40px] items-center text-blue-400 hover:underline md:min-h-0">
            {hidden} more →
          </Link>
        </p>
      )}
      <p className="mt-2 text-[10px] text-faint">
        {/* each schedule counts once, at its next charge: the export carries only the next three
            dates, so a weekly or biweekly cadence that fires twice inside 30 days is NOT summed
            twice — the wording says "next charge of each", never "total spend" */}
        {listed.length > 0 && <>≈ {fmtUSD0(total)} = the next charge of {listed.length === 1 ? "the schedule"
          : `each of the ${listed.length} schedules`} listed{hidden > 0 ? `, not the ${hidden} more` : ""} · </>}
        detected schedules only, judged against today and each account's own data — "data stale"
        means that account's transactions do not reach the expected date yet, not a missed payment.
      </p>
    </Card>
  );
}
