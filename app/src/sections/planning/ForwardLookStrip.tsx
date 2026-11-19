// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useMemo } from "react";
import { NO_BALANCES_YET, NO_TRANSACTIONS_YET, runRateWaits } from "../../components/NoData";
import RequireData from "../../components/RequireData";
import { StatCard } from "../../components/ui";
import { useData } from "../../data/DataContext";
import { useSnapshots } from "../../data/useSnapshots";
import { dataFirstDay, monthInProgress, runRateMonths } from "../../lib/agg";
import { useEfundOverrides } from "../../lib/efundOverrides";
import { emergencyWindow } from "../../lib/emergency";
import { fmtDelta, fmtMonths, fmtUSD0 } from "../../lib/format";
import { bearCaseClasses, bearCaseTotal, lumpyBillsTotal, SHOCK_DEFAULTS } from "../../lib/forwardLook";
import { cashRunway } from "../../lib/lifeSummary";
import { obligationMonths } from "../../lib/obligations";
import { useVocab } from "../../lib/useVocab";
import { SCROLL_TARGETS, scrollToCard } from "../../lib/scrollTargets";

/** "A, B or C" */
const orList = (names: readonly string[]): string =>
  names.length <= 1 ? names.join("") : `${names.slice(0, -1).join(", ")} or ${names[names.length - 1]}`;

/** DOM ids of the cards the KPIs below scroll to (lib/scrollTargets.ts owns them, the scroll and the `?card=` query
 *  key that lands on one). Tools.tsx stamps them on the cards, smoke.test.tsx asserts both are on the real page. A
 *  KPI scrolls to a card on this same page by element id (a hash link cannot target an in-page anchor under
 *  HashRouter); a card that is not rendered (its data is gated) leaves nothing to scroll to, and the click is then a
 *  no-op by design. */
export { SCROLL_TARGETS };

/** Planning header: the Life "forward look" in three numbers, each linking to its full card */
export default function ForwardLookStrip() {
  const { data } = useData();
  // the newest snapshot; null for a household with none yet: no cash and no net worth, and the tiles say so
  const current = useSnapshots().latest;
  // the same essential toggles the gauge below applies (device-local, lib/efundOverrides), so the
  // KPI and the gauge always show the same months
  const overrides = useEfundOverrides();
  const vocab = useVocab();
  // the essentials average the gauge's window (lib/emergency.ts emergencyWindow: the six full months before the month in
  // progress, or as many as there are, none below agg.ts RUN_RATE_MIN_MONTHS)
  const inProgress = useMemo(() => monthInProgress(data), [data]);
  const runway = useMemo(
    () => cashRunway(vocab, data.transactions, current,
      emergencyWindow(data.cashflow, inProgress, dataFirstDay(data.transactions)), overrides),
    [vocab, data.transactions, data.cashflow, current, inProgress, overrides]);
  const bear = useMemo(() => bearCaseTotal(current), [current]);
  // the twelve months Spending → Recurring's obligations chart covers (the months after the month in progress), so the
  // totals agree; with no transaction there is nothing renewals were found in, so the total is unknown, never $0; with
  // nothing detected, or no renewal due in the window, there is nothing to total: "—" and which of the two
  const billMonths = useMemo(() => obligationMonths(inProgress), [inProgress]);
  const unknown = billMonths.length === 0 || data.transactions.length === 0;
  const lumpy = useMemo(() => (unknown ? null : lumpyBillsTotal(data.recurring, billMonths)),
    [data.recurring, unknown, billMonths]);
  const lumpySub = unknown ? NO_TRANSACTIONS_YET
    : data.recurring.length === 0 ? "none detected yet"
    : lumpy === null ? "no annual or quarterly renewal due"
    : "annual and quarterly renewals — timeline in Spending → Recurring";
  // nothing essential to divide by is "unknown", not "0 months of runway"
  const months = runway.months;
  // the runway needs the snapshot's cash and the essentials of enough full months: the tile names what is missing in
  // short (the snapshot first; the cards below say it in full); with too few full months it says what it needs and how
  // many there are
  const dataMissing = runway.cash === null || runway.essential === null;
  const runwaySub = runway.cash === null ? NO_BALANCES_YET
    : runway.essential === null ? (data.transactions.length === 0 ? NO_TRANSACTIONS_YET
      : runRateWaits(runRateMonths(data.cashflow, inProgress, dataFirstDay(data.transactions)).length))
    : months !== null ? `${fmtUSD0(runway.cash)} cash ÷ ${fmtUSD0(runway.essential)}/mo essentials — gauge below`
    : runway.rowCount ? "no row is marked essential — toggles in the gauge below"
    : "no essential-spending history yet";
  return (
    <div data-testid="kpis-forward-look" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {/* with its data missing the emergency-fund card below says the same thing, so, as the bear case, there is nothing
          to scroll to */}
      <StatCard testId="kpi-cash-runway" label="Cash runway"
        value={fmtMonths(months)}
        tone={months === null ? undefined : months < 3 ? "warn" : "pos"}
        sub={runwaySub}
        onClick={dataMissing ? undefined : () => scrollToCard(SCROLL_TARGETS.runway)} />
      {/* no snapshot: no net worth to shock, and nothing to scroll to (the stress test says the same) */}
      {bear === null || current === null
        ? <StatCard testId="kpi-bear-case" label="Bear-case net worth" value="—" sub={NO_BALANCES_YET} />
        : <StatCard testId="kpi-bear-case" label="Bear-case net worth" value={fmtUSD0(bear)}
            // a warning tone needs a fall (of a dollar or more): a household holding nothing the case moves keeps its net
            // worth, and says why
            tone={current.total - bear >= 0.5 ? "warn" : undefined}
            sub={Math.abs(current.total - bear) < 0.5
              ? `nothing held falls in this case (no ${orList(bearCaseClasses())}) — sliders in the stress test below`
              : `${fmtDelta(bear - current.total)} if stocks fall ${-SHOCK_DEFAULTS.eq}% and crypto/private ${-SHOCK_DEFAULTS.crypto}% — sliders in the stress test below`}
            onClick={() => scrollToCard(SCROLL_TARGETS.bearCase)} />}
      <RequireData compact files={["recurring.json"]} what="The lumpy-bills total">
        <StatCard testId="kpi-big-bills" label="Lumpy bills, next 12 months" value={lumpy === null ? "—" : fmtUSD0(lumpy)}
          sub={lumpySub} href="#/spending/recurring" />
      </RequireData>
    </div>
  );
}
