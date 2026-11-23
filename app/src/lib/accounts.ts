// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { AccountMeta } from "../data/types";

/** Cash-like: an account whose class is Cash. The class is the source of truth for what the balance IS: a brokerage's
 *  cash account is kind "brokerage" as feed plumbing but holds cash, and a savings account that states another class
 *  (a 529, say) is not cash. Both maps that reach this carry the class the engine resolves (accounts.yaml's, else the
 *  kind's default, `defaultClass`): accounts.json's AccountMeta and balances_timeseries.json's accounts map. */
export function isCashLike(a: { asset_class: string }): boolean {
  return a.asset_class === "Cash";
}

/** An investment account: a brokerage-kind account whose money is not cash (a brokerage's cash account is cash). */
export function isInvestmentAccount(a: { kind: string; asset_class: string }): boolean {
  return a.kind === "brokerage" && !isCashLike(a);
}

/** The class each kind's rows take when accounts.yaml states none: etl/models.py DEFAULT_CLASS_BY_KIND (pinned equal by
 *  tests/test_exports.py). `wallet` is no kind the engine writes; a wallet holds crypto. */
export const DEFAULT_CLASS_BY_KIND: Readonly<Record<AccountMeta["kind"], string>> = {
  checking: "Cash", savings: "Cash", brokerage: "Stocks/ETF", "credit-card": "Other Debt", loan: "Other Debt",
  wallet: "Crypto",
};

/** The class an account with no stated class takes, as the engine resolves it: Mortgage for the `mortgage: true` loan,
 *  else its kind's default. */
export function defaultClass(kind: AccountMeta["kind"], mortgage: boolean): string {
  return kind === "loan" && mortgage ? "Mortgage" : DEFAULT_CLASS_BY_KIND[kind];
}
