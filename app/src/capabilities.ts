// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// The capability manifest: every card, chart and KPI the dashboard
// renders, pinned to exactly ONE route by its data-testid. smoke.test.tsx mounts every route
// against the real exports and findByTestId()s each id pinned to it; a trailing test refuses a
// row whose route no longer exists. Moving a card = moving its row here in the same commit.
// This file is the source of truth for ids and homes. A row with no `when` is on its route for every household (the
// route matrix's `cards` check), unless a stand-in row that names it in `insteadOf` is on screen in its place, or the
// first-run page or the module-off page stands in for the route's page.
import type { FirstRunState } from "./lib/firstRun";

/** content that exists only under a condition the per-route loop cannot create; the dedicated
 *  smoke `it` that creates it asserts these ids instead. `first-run`: a card of the first-run page (sections/FirstRun.tsx),
 *  which shows only while the household's exports hold nothing; the route matrix asserts those on every route the page
 *  stands in for, on the demo household that has nothing, in both states. `module-off`: a card of the module-off page
 *  (components/ModuleOff.tsx), which shows only on a route whose page is switched off; the route matrix asserts it on
 *  every such route, on every demo household that has one */
export type CapabilityWhen = "scenario" | "drill" | "data" | "first-run" | "module-off";

export interface Capability {
  id: string; route: string; when?: CapabilityWhen;
  /** a stand-in: the rows of its own route (rows with no `when`) this card replaces while it is on screen. Such a card
   *  is itself `when: "data"`; the rows it names may be absent only while it is there, and must be there otherwise. */
  insteadOf?: readonly string[];
  /** a `first-run` row shown in one state only (none: in both) */
  firstRun?: FirstRunState;
}

/** `/` -> page-home; `/accounts/:id` -> page-accounts-id. Stamped by App.tsx pageRoute(). */
export function pageTestId(route: string): string {
  if (route === "/") return "page-home";
  return "page-" + route.replace(/^\//, "").replace(/:/g, "").replace(/\//g, "-");
}

export function hubTestId(route: string): string {
  return "hub-" + route.replace(/^\//, "").replace(/\//g, "-");
}

const at = (route: string, ids: string[], when?: CapabilityWhen): Capability[] =>
  ids.map((id) => (when ? { id, route, when } : { id, route }));

export const CAPABILITIES: readonly Capability[] = [
  ...at("/", ["kpis-home", "kpi-savings-rate-ttm", "card-snapshot-deltas", "card-month-to-date",
    "card-decomposition", "card-networth-history", "card-allocation-donut",
    "card-next-30-days", "card-data-quality"]),
  ...at("/data", ["card-freshness", "card-account-freshness", "card-quality-report", "card-ingest-read", "card-open-questions",
    "card-learned-rules", "card-assumptions-in-force", "card-projection-defaults", "card-saved-views",
    "card-privacy", "card-caveats"]),
  ...at("/data", ["card-split-rules"], "data"),
  ...at("/settings", ["card-modules", "card-manual-assets", "card-ai-categorization", "card-elsewhere", "card-about",
    "card-sign-out"]),
  ...at("/trends", ["card-trend-savings", "card-trend-burn", "card-trend-allocation", "card-trend-liquidity"]),
  ...at("/cash-flow", ["kpis-cashflow", "kpi-savings-rate-12m", "kpi-monthly-burn", "card-cashflow-main", "card-income-by-source"]),
  ...at("/cash-flow/income-statement", ["card-sankey", "card-pnl"]),
  ...at("/cash-flow/budget", ["card-budget", "card-fixed-vs-discretionary", "card-payroll"]),
  ...at("/spending", ["card-spend-by-category", "card-top-vendors", "card-category-breakdown",
    "card-period-compare", "card-weekday-spend", "card-spend-calendar"]),
  ...at("/spending", ["card-vendor-drill"], "drill"),
  ...at("/spending/transactions", ["card-transactions"]),
  ...at("/spending/reports", ["card-report-builder", "card-report-result"]),
  ...at("/spending/recurring", ["kpis-recurring", "card-obligations", "card-recurring-table"]),
  // the asset-class targets card is there for every household: with no target stated it says so and offers the editor
  ...at("/accounts/investments", ["kpis-investments", "card-concentration", "card-target-mix", "card-class-drift",
    "card-holdings", "card-investment-balances"]),
  ...at("/accounts/liabilities", ["kpis-liabilities", "kpi-total-debt", "kpi-mortgage-balance", "kpi-card-debt",
    "kpi-margin-debt", "kpi-available-credit", "card-mortgage-summary", "card-card-balances", "card-utilization"]),
  // with no credit-card account in the balance history, the one card that says so stands in for the two card charts
  { id: "card-no-credit-cards", route: "/accounts/liabilities", when: "data",
    insteadOf: ["card-card-balances", "card-utilization"] },
  ...at("/accounts/:id", ["card-account-header", "card-account-balance-history", "card-ledger-drift", "card-account-transactions"]),
  ...at("/accounts", ["kpis-balances", "kpi-illiquid", "card-liquidity", "card-balance-sheet-assets",
    "card-balance-sheet-liabilities", "card-current-balances", "card-cash-balances"]),
  ...at("/planning/home", ["kpis-mortgage", "card-mortgage-assumptions", "card-mortgage-trajectory",
    "card-prepay-vs-invest"]),
  ...at("/planning/second-house", ["card-second-house-empty"]),
  ...at("/planning/second-house", ["card-scenario-tabs", "card-deal-inputs", "kpis-second-house",
    "card-funding-plan", "card-cost-breakdown", "card-rental-model", "card-rate-sensitivity",
    "card-down-payment-sweep", "card-equity-curve", "kpis-guardrails", "card-scenario-comparison"], "scenario"),
  ...at("/planning", ["kpis-forward-look", "kpi-cash-runway", "kpi-bear-case", "kpi-big-bills",
    "card-forecast", "card-retirement", "card-networth-projection", "card-emergency-fund",
    "card-market-shock", "card-spending-cut"]),
  ...at("/review", ["card-review-inbox"]),
  ...at("/review", ["card-review-conflicts"], "data"),
  // the first-run page stands in for every page but Data and Settings (lib/firstRun.ts): its rows are pinned to Home,
  // where a new household lands, and are on every route the page stands in for while the household is in their state.
  // The heading card is in both states; the steps say how to set up, the summary what is configured. (With no export at
  // all the app shows the same page outside its routes, the heading card and the steps, which no route matrix sees.)
  { id: "card-first-run", route: "/", when: "first-run" },
  { id: "card-first-run-steps", route: "/", when: "first-run", firstRun: "setup" },
  { id: "card-first-run-summary", route: "/", when: "first-run", firstRun: "waiting" },
  // the module-off page stands in for a page whose module is off or that needs what the household lacks (App.tsx
  // closedRoutes): its one card is pinned to the module's page and is on every route the page stands in for
  { id: "card-module-off", route: "/planning/second-house", when: "module-off" },
];
