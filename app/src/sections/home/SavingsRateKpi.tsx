// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { NO_FULL_MONTH } from "../../components/NoData";
import { StatCard } from "../../components/ui";
import { fmtPct, rateTone } from "../../lib/format";
import { belowRateFloor, printableRate, savingsRateLabel, shortfallText } from "../../lib/lifeSummary";

/** Home's hero savings rate. `months` is how many full months actually fed the rate (12 once the
 *  history is long enough), so the label never claims a trailing year it does not have; with no full month there is no
 *  window at all: the label names no count, the value is "—" and the line says there is not enough history yet. A null
 *  rate (no income in the window) reads "—" in the neutral tone, not green; so does a rate below RATE_FLOOR (spending
 *  over twice the income), whose line says the shortfall in dollars (`net`, the window's average month). It is cash
 *  flow, not a snapshot, so a ?snap= rewind does not move it: `rewoundTo` (the viewed snapshot's date) makes it
 *  say it is still the latest, beside KPIs that are as of that date. */
export default function SavingsRateKpi({ rate, months, net, rewoundTo }: {
  rate: number | null; months: number; net: number; rewoundTo?: string;
}) {
  const label = months === 0 ? "Savings rate" : savingsRateLabel(months);
  const shown = printableRate(rate);
  const span = months === 0 ? NO_FULL_MONTH
    : `${belowRateFloor(rate) ? `${shortfallText(net)} · ` : ""}trailing ${months} full month${months === 1 ? "" : "s"} · cash basis`;
  return (
    <StatCard testId="kpi-savings-rate-ttm" label={rewoundTo ? `${label} · latest` : label}
      value={months === 0 ? "—" : fmtPct(shown, 0)} tone={months === 0 ? undefined : rateTone(shown)}
      sub={`${span}${rewoundTo ? ` · not rewound to ${rewoundTo}` : ""}`} href="#/cash-flow" />
  );
}
