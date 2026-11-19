// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useMemo, useState } from "react";
import { BudgetGridTable, FixedVsDiscretionaryCard, monthsPhrase } from "../components/BudgetGrid";
import SplitNote from "../components/SplitNote";
import { MaskedNumberInput } from "../components/inputs";
import { Code, NotEnoughHistory, NoTransactions } from "../components/NoData";
import PrefEditorRow from "../components/PrefEditorRow";
import RequireData from "../components/RequireData";
import { Card, DoneButton, EditButton, LocalOnlyNotice } from "../components/ui";
import { useData } from "../data/DataContext";
import { monthBounds } from "../filters/dateRange";
import { spendingLink } from "../filters/filters";
import { fullMonths, monthInProgress, prevMonth } from "../lib/agg";
import { BUDGET_FLOOR, budgetGrid, budgetRows } from "../lib/budgets";
import { clock } from "../lib/clock";
import { useEfundOverrides } from "../lib/efundOverrides";
import { fmtUSD0 } from "../lib/format";
import { renameBudgets } from "../lib/prefs";
import { SCROLL_TARGETS, useScrollToCardParam } from "../lib/scrollTargets";
import { useIsMobile } from "../lib/useMediaQuery";
import { useVocab } from "../lib/useVocab";

/** Cash Flow → Budget: the budget variance grid and the family's payroll facts (both in-app editable). A `?card=`
 *  link lands on the budget card (lib/scrollTargets.ts). */
export default function Budget() {
  useScrollToCardParam();
  return (
    <RequireData files={["cashflow_monthly.json"]} what="Budget">
      <div className="space-y-4">
        <BudgetCard />
        <PayrollCard />
      </div>
    </RequireData>
  );
}

/** Payroll & retirement contributions — powers the "total savings rate".
 *  Dormant until phases are entered; bank data can't see pre-tax 401(k)
 *  deferrals, so these facts come from the family. */
function PayrollCard() {
  const { data, prefs } = useData();
  const owners = data.assumptions.household.people.filter((p) => p.role === "owner");
  const phases = prefs.doc.payroll.phases;
  const [open, setOpen] = useState(phases.length > 0);

  const update = (i: number, patch: Partial<(typeof phases)[number]>) => {
    prefs.setPayrollPhases(phases.map((p, k) => (k === i ? { ...p, ...patch } : p)));
  };
  const add = () => {
    prefs.setPayrollPhases([...phases, {
      person: owners[0]?.name ?? "", from: clock.today(data.assumptions.household.timezone).slice(0, 7), to: null,   // the household's month
      gross_annual: 0, employee_401k_pct: 0, employer_match_pct: 0,
    }]);
    setOpen(true);
  };
  const remove = (i: number) => prefs.setPayrollPhases(phases.filter((_, k) => k !== i));

  if (!open && phases.length === 0) {
    return (
      <p data-testid="card-payroll" className="text-[10px] text-faint">
        Tip: the savings rate on the{" "}
        <a href="#/cash-flow" className="text-emerald-400 underline hover:text-emerald-300">
          Cash Flow overview
        </a>{" "}
        only sees take-home pay —{" "}
        <button onClick={add} className="text-emerald-400 underline hover:text-emerald-300">
          add payroll facts
        </button>{" "}
        (gross salary, 401(k) %, employer match) to see your true savings rate
        including retirement contributions.
      </p>
    );
  }
  return (
    <Card testId="card-payroll" title="Payroll & retirement contributions"
      right={<EditButton onClick={add}>+ Add phase</EditButton>}>
      <p className="mb-2 text-[11px] text-zinc-500">
        Per employment phase: gross salary and pre-tax retirement percentages. Powers
        the "total savings rate" (take-home basis + 401(k) + match; taxes not modeled).
        Months are inclusive; leave "to" blank for a current job.
        {prefs.localOnly && <LocalOnlyNotice refused={prefs.refused} />}
      </p>
      <div className="space-y-2">
        {phases.map((p, i) => (
          <div key={i}
            className="grid grid-cols-2 items-end gap-x-3 gap-y-1.5 rounded-lg border border-zinc-800 p-2 sm:grid-cols-7">
            <label className="flex flex-col gap-0.5 text-[10px] text-zinc-500">
              Person
              <select value={p.person} onChange={(e) => update(i, { person: e.target.value })}
                className="rounded border border-zinc-700 bg-zinc-900 px-1.5 py-1 text-base text-zinc-200 sm:py-0.5 sm:text-xs">
                {owners.map((o) => <option key={o.name}>{o.name}</option>)}
              </select>
            </label>
            <label className="flex flex-col gap-0.5 text-[10px] text-zinc-500">
              From (YYYY-MM)
              <input type="month" value={p.from}
                onChange={(e) => update(i, { from: e.target.value })}
                className="rounded border border-zinc-700 bg-zinc-900 px-1.5 py-1 text-base text-zinc-200 sm:py-0.5 sm:text-xs" />
            </label>
            <label className="flex flex-col gap-0.5 text-[10px] text-zinc-500">
              To (blank = now)
              <input type="month" value={p.to ?? ""}
                onChange={(e) => update(i, { to: e.target.value || null })}
                className="rounded border border-zinc-700 bg-zinc-900 px-1.5 py-1 text-base text-zinc-200 sm:py-0.5 sm:text-xs" />
            </label>
            <label className="flex flex-col gap-0.5 text-[10px] text-zinc-500">
              Gross $/yr
              <MaskedNumberInput type="number" min={0} step={5000} value={p.gross_annual || ""}
                onChange={(e) => update(i, { gross_annual: Number(e.target.value) || 0 })}
                className="rounded border border-zinc-700 bg-zinc-900 px-1.5 py-1 text-right text-base tabular-nums text-zinc-200 sm:py-0.5 sm:text-xs" />
            </label>
            <label className="flex flex-col gap-0.5 text-[10px] text-zinc-500">
              401(k) %
              <input type="number" min={0} max={100} step={0.5} value={p.employee_401k_pct || ""}
                onChange={(e) => update(i, { employee_401k_pct: Number(e.target.value) || 0 })}
                className="rounded border border-zinc-700 bg-zinc-900 px-1.5 py-1 text-right text-base tabular-nums text-zinc-200 sm:py-0.5 sm:text-xs" />
            </label>
            <label className="flex flex-col gap-0.5 text-[10px] text-zinc-500">
              Match %
              <input type="number" min={0} max={100} step={0.5} value={p.employer_match_pct || ""}
                onChange={(e) => update(i, { employer_match_pct: Number(e.target.value) || 0 })}
                className="rounded border border-zinc-700 bg-zinc-900 px-1.5 py-1 text-right text-base tabular-nums text-zinc-200 sm:py-0.5 sm:text-xs" />
            </label>
            <button onClick={() => remove(i)}
              className="justify-self-start rounded-md border border-zinc-800 px-2 py-1 text-[11px] text-zinc-500 hover:bg-zinc-900 hover:text-red-300">
              Remove
            </button>
          </div>
        ))}
        {phases.length === 0 && (
          <p className="text-xs text-zinc-500">No phases yet — add one to activate the true savings rate.</p>
        )}
      </div>
    </Card>
  );
}

/** the month columns a wider screen shows; a phone shows the last three of them, with the same rows */
const WIDE_SPAN = 6;

/** the budget grid: each category's actual for the last full months against its baseline (the
 *  trailing-12 median, unless a budget is pinned in-app or in assumptions.yaml), then the
 *  fixed-vs-discretionary split. Phones get three month columns, wider screens six; both list the rows the six decide. */
function BudgetCard() {
  const { data } = useData();
  const [editing, setEditing] = useState(false);
  const phone = useIsMobile();
  const span = phone ? 3 : WIDE_SPAN;
  // this device's "essential" toggles (Planning's emergency-fund card), the same source every other
  // essential figure reads (lib/efundOverrides)
  const essentialOverrides = useEfundOverrides();
  // the grid's columns are the full months before the month in progress (lib/agg.ts monthInProgress); the editor's
  // automatic baselines are the newest full month's (null with no month in progress: no baseline to show)
  const inProgress = useMemo(() => monthInProgress(data), [data]);
  const month = inProgress === null ? null : prevMonth(inProgress);
  // how many full months the household has: what an empty grid says it lacks
  const full = fullMonths(data.cashflow, inProgress).length;
  const vocab = useVocab();
  const grid = useMemo(
    () => budgetGrid({ vocab, cashflow: data.cashflow, txns: data.transactions, overrides: data.assumptions.budgets,
                       essentialOverrides, inProgress, months: span, rowMonths: WIDE_SPAN }),
    [vocab, data.cashflow, data.transactions, data.assumptions.budgets, essentialOverrides, inProgress, span]);
  if (editing) {
    return <BudgetEditor month={month} onDone={() => setEditing(false)} />;
  }
  if (!grid.rows.length) {
    // what the grid lacks, in the words that are true of this household: no transaction at all (the page's long line),
    // no full month, one full month (nothing before it to take a baseline from), or full months whose categories all
    // stay under the floor; the title names the months only where that is what is missing
    const noTransactions = data.transactions.length === 0;
    const title = noTransactions ? "Budget check" : full === 0 ? "Budget check — no full month of cash-flow data yet"
      : grid.months.length === 0 ? "Budget check — one full month so far" : "Budget check";
    return (
      <>
        <Card testId="card-budget" id={SCROLL_TARGETS.budget} title={title}
          right={<EditButton onClick={() => setEditing(true)}>Edit budgets</EditButton>}>
          {noTransactions ? <NoTransactions /> : full === 0 ? (
            <NotEnoughHistory>each full month is compared with the median of the months before it (or your pinned
              budget), so the grid appears once two full months are in.</NotEnoughHistory>
          ) : (
            <p className="text-xs text-zinc-500">
              {grid.months.length === 0
                ? "Each full month is compared with the median of the months before it (or your pinned budget), so the grid needs at least two full months of cash-flow coverage: one full month has no baseline yet. It appears once a second one lands."
                : `No category reached ${fmtUSD0(BUDGET_FLOOR)} a month in these months, so there is nothing to compare yet.`}
            </p>
          )}
        </Card>
        <FixedVsDiscretionaryCard grid={grid} vocab={vocab} noTransactions={noTransactions} />
      </>
    );
  }
  return (
    <>
      <Card testId="card-budget" id={SCROLL_TARGETS.budget} title={`Budget — last ${monthsPhrase(grid.months.length)} vs typical`}
        right={<EditButton onClick={() => setEditing(true)}>Edit budgets</EditButton>}>
        <BudgetGridTable grid={grid}
          linkFor={(category, m) => { const [from, to] = monthBounds(m); return spendingLink({ categories: [category], from, to }); }} />
        <p className="mt-2 text-[10px] text-faint">
          "Typical" is the median of the trailing 12 full months per category (or your pinned budget).
          The smaller line under each amount is how far it is from typical: + over (red), − under (green); "—" = nothing spent and no baseline yet.
          Pin a category with “Edit budgets” (or <Code>budgets:</Code> in <Code>assumptions.yaml</Code>).
        </p>
        <SplitNote categories={grid.rows.map((r) => r.category)} />
      </Card>
      <FixedVsDiscretionaryCard grid={grid} vocab={vocab} noTransactions={data.transactions.length === 0} />
    </>
  );
}

/** Edit mode: every spending category, blank = the self-calibrating auto
 *  baseline. Saves per keystroke (debounced to the prefs store). A budget saved under a category's
 *  OLD name belongs to the renamed category, so the editor reads the renamed view of the doc and
 *  every write passes the renames along (lib/prefs.ts withBudget drops the old-name keys). */
export function BudgetEditor({ month, onDone }: { month: string | null; onDone: () => void }) {
  const { data, prefs } = useData();
  const renames = data.assumptions.category_renames;
  const vocab = useVocab();
  const pinned = useMemo(() => renameBudgets(prefs.doc.budgets, renames), [prefs.doc.budgets, renames]);
  // auto medians for placeholders — the same math the display card uses
  const autoOf = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of month === null ? [] : budgetRows(vocab, data.cashflow, month, {})) m.set(r.category, r.budget);
    return m;
  }, [vocab, data.cashflow, month]);
  const categories = useMemo(
    () => Object.keys(data.assumptions.taxonomy ?? {})
      .filter((c) => !vocab.isTypeDriven(c))
      .sort((a, b) => {
        const aSet = pinned[a] !== undefined ? 1 : 0;
        const bSet = pinned[b] !== undefined ? 1 : 0;
        if (aSet !== bSet) return bSet - aSet;               // pinned first
        return (autoOf.get(b) ?? 0) - (autoOf.get(a) ?? 0);  // then by typical spend
      }),
    [vocab, data.assumptions.taxonomy, pinned, autoOf]);
  return (
    <Card testId="card-budget" id={SCROLL_TARGETS.budget} title="Edit monthly budgets"
      right={<DoneButton onClick={onDone} />}>
      <p className="mb-2 text-[11px] text-zinc-500">
        Blank = automatic baseline (median of the trailing 12 full months, shown as the
        placeholder). Saved for the whole family, instantly — no config edit needed.
        {prefs.localOnly && <LocalOnlyNotice what="budgets" refused={prefs.refused} />}
      </p>
      <div className="grid gap-x-6 gap-y-1.5 sm:grid-cols-2">
        {categories.map((c) => {
          const set = pinned[c];
          const auto = autoOf.get(c);
          return (
            <PrefEditorRow key={c} label={c} labelTitle={c} labelCls="w-32"
              min={0} step={50} money
              value={set ?? ""}
              placeholder={auto !== undefined ? String(Math.round(auto)) : "—"}
              onChange={(v) => prefs.setBudget(c, v, renames)}
              before={<span className="text-faint">$</span>}
              onClear={set !== undefined ? () => prefs.setBudget(c, null, renames) : undefined}
              clearTitle="clear — back to the automatic baseline" />
          );
        })}
      </div>
    </Card>
  );
}
