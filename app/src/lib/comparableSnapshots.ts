// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { AccountMeta, AssetRow, Snapshot } from "../data/types";
import { countOf } from "./format";
import { DEBT_CLASSES } from "./tokens";

/** How many net-worth series a snapshot misses besides the mortgage. A snapshot that leaves the mortgage out
 *  (`mortgage_included: false`) lists the mortgage account in `missing`, and the † notes already say so, so it is not
 *  counted. Never below 0: an export from before `missing` existed lists nothing (the loader's default) whatever its
 *  mortgage flag says. */
function missingBesidesMortgage(s: Snapshot): number {
  return Math.max(0, s.missing.length - (s.mortgage_included ? 0 : 1));
}

/** Splits the snapshots (oldest first, as exported; the last is the newest, as every page reads it) into those
 *  comparable with the newest and the partial ones before them. A snapshot is comparable when, the mortgage aside, it
 *  misses nothing the newest has: a series only ever starts (an account is carried once it has reported), so an
 *  older snapshot misses at least what a newer one misses, and equal counts mean equal sets. A partial month would
 *  show the day an account was added as a jump in net worth. The newest is always comparable; both lists keep the
 *  input order. */
export function comparableSnapshots(snaps: readonly Snapshot[]): { comparable: Snapshot[]; partial: Snapshot[] } {
  const comparable: Snapshot[] = [];
  const partial: Snapshot[] = [];
  const latest = snaps[snaps.length - 1];
  if (latest === undefined) return { comparable, partial };
  const want = missingBesidesMortgage(latest);
  for (const s of snaps) (s === latest || missingBesidesMortgage(s) === want ? comparable : partial).push(s);
  return { comparable, partial };
}

/** A month end the history leaves out, and the accounts it has no balance for that the newest snapshot has */
export interface LeftOutMonthEnd { date: string; misses: string[] }

/** The month ends the history leaves out (`partial`, from `comparableSnapshots`), NEWEST first, each with what it misses:
 *  the display names in its `missing` that the newest snapshot (`latest`) has a balance for, in the export's order. An
 *  account the newest snapshot misses too (configured, never reported) is no reason a month end is left out, so it is not
 *  listed with one (Data says it once, on its own). */
export function leftOutMonthEnds(partial: readonly Snapshot[], latest: Snapshot | null): LeftOutMonthEnd[] {
  const still = new Set(latest?.missing ?? []);
  return [...partial].reverse().map((s) => ({ date: s.snapshot_date, misses: s.missing.filter((n) => !still.has(n)) }));
}

/** the account kinds whose balance is a debt: a loan (the configured mortgage among them) and a credit card */
const DEBT_KINDS: ReadonlySet<string> = new Set(["loan", "credit-card"]);

/** The accounts a snapshot misses (its `missing`, display names: configured, their first balance not arrived) that would
 *  hold assets: all but those whose account (`accounts`, found by name, as accounts.json gives its kind) is of a debt
 *  kind. A debt is in no share of assets, so a snapshot that misses only the mortgage has every asset it will have. A
 *  name the registry does not know is counted: nothing says it holds no asset. None with no snapshot. */
export function missingAssetAccounts(s: Pick<Snapshot, "missing"> | null,
                                     accounts: readonly Pick<AccountMeta, "name" | "kind">[]): string[] {
  const kinds = new Map(accounts.map((a) => [a.name, a.kind]));
  return (s?.missing ?? []).filter((n) => !DEBT_KINDS.has(kinds.get(n) ?? ""));
}

/** The one mark a figure of today's balances carries while the snapshot misses `n` accounts it would hold, so a figure
 *  of an incomplete total is not read as the whole: "of the assets reported so far — 2 accounts not yet reported" for a
 *  share of assets (`of: "assets"`: Home's liquid share and allocation donut, counting `missingAssetAccounts`), "of the
 *  balances reported so far — …" for a card that prints the debts too (`of: "balances"`: Balances' Liquidity card,
 *  counting every account the snapshot misses). null when `n` is 0. */
export function notYetReported(n: number, of: "assets" | "balances"): string | null {
  return n === 0 ? null : `of the ${of} reported so far — ${countOf(n, "account")} not yet reported`;
}

/** The debt rows a comparison across snapshots folds into their account: a row of a debt class whose account the
 *  snapshot also holds a row of another class for (a live margin account's loan beside its positions). A month end holds
 *  such an account as one row at the feed's balance, net of the loan. */
export function foldedDebt(s: Snapshot): AssetRow[] {
  const others = new Set(s.rows.filter((r) => r.account_id !== null && !DEBT_CLASSES.has(r.asset_class)).map((r) => r.account_id));
  return s.rows.filter((r) => DEBT_CLASSES.has(r.asset_class) && r.account_id !== null && others.has(r.account_id));
}

const cents = (dollars: number): number => Math.round(dollars * 100);

/** The newest snapshot on the month ends' footing: a live margin account, which the newest snapshot holds GROSS (its
 *  positions plus a debt row for the loan), becomes the one row every month end holds for it, at the feed's balance net
 *  of the loan - in the class of its largest positions row (`classOf` names the account's own class where the caller
 *  has it), with that row's owner, liquidity and date. The snapshot's class and owner totals, `liquid_total`,
 *  `assets_total` and `liabilities_total` follow the rows, in whole cents; its total does not move. A snapshot with
 *  nothing to fold is returned as it is. Every series or change that sets the newest snapshot beside month ends reads
 *  it; a "now" tile and the balance sheet read the snapshot as it is. */
export function onMonthEndFooting(s: Snapshot, classOf?: (accountId: string) => string | undefined): Snapshot {
  const ids = new Set(foldedDebt(s).map((r) => r.account_id as string));
  if (ids.size === 0) return s;
  const byClass = new Map(Object.entries(s.by_class).map(([k, v]) => [k, cents(v)]));
  const byOwner = new Map(Object.entries(s.by_owner).map(([k, v]) => [k, cents(v)]));
  let liquid = cents(s.liquid_total), assets = cents(s.assets_total), liabilities = cents(s.liabilities_total);
  // a class or owner the fold empties (the loan's Margin class) leaves the totals, as no month end holds it
  const touchedClass = new Set<string>(), touchedOwner = new Set<string>();
  const take = (r: AssetRow, sign: 1 | -1) => {
    const c = sign * cents(r.value);
    byClass.set(r.asset_class, (byClass.get(r.asset_class) ?? 0) + c);
    byOwner.set(r.owner, (byOwner.get(r.owner) ?? 0) + c);
    touchedClass.add(r.asset_class);
    touchedOwner.add(r.owner);
    if (r.liquid) liquid += c;
    if (r.value > 0) assets += c; else liabilities += c;
  };
  const merged: AssetRow[] = [];
  for (const id of ids) {
    const rows = s.rows.filter((r) => r.account_id === id);
    const lead = rows.filter((r) => !DEBT_CLASSES.has(r.asset_class)).reduce((a, b) => (b.value > a.value ? b : a));
    for (const r of rows) take(r, -1);
    const row: AssetRow = { ...lead, asset_class: classOf?.(id) ?? lead.asset_class,
      value: rows.reduce((c, r) => c + cents(r.value), 0) / 100, source: "simplefin_balance" };
    take(row, 1);
    merged.push(row);
  }
  const back = (m: Map<string, number>, touched: Set<string>) =>
    Object.fromEntries([...m].filter(([k, c]) => c !== 0 || !touched.has(k)).map(([k, c]) => [k, c / 100]));
  return {
    ...s, rows: [...s.rows.filter((r) => r.account_id === null || !ids.has(r.account_id)), ...merged],
    by_class: back(byClass, touchedClass), by_owner: back(byOwner, touchedOwner),
    liquid_total: liquid / 100, assets_total: assets / 100, liabilities_total: liabilities / 100,
  };
}

/** The one line under a history whose last point is on the month ends' footing (`onOneFooting` `folded`); `where` names
 *  today's figures it can differ from ("above", "on Home and Balances") */
export const footingNote = (where: string): string =>
  `Month ends hold a margin account net of its loan; the last point is drawn the same way, so it can differ from today's `
  + `figures ${where}, which count the positions and the loan separately.`;

/** A history of snapshots (oldest first) with its newest on the month ends' footing (`onMonthEndFooting`), and whether
 *  that moved anything: a card whose last point is therefore not today's figure says so (`footingNote`). With fewer
 *  than two snapshots there is no month end to set it beside, and it stays as it is. */
export function onOneFooting(snaps: readonly Snapshot[], classOf?: (accountId: string) => string | undefined):
    { snaps: Snapshot[]; folded: boolean } {
  const latest = snaps[snaps.length - 1];
  if (latest === undefined || snaps.length < 2) return { snaps: [...snaps], folded: false };
  const footed = onMonthEndFooting(latest, classOf);
  return footed === latest ? { snaps: [...snaps], folded: false } : { snaps: [...snaps.slice(0, -1), footed], folded: true };
}
