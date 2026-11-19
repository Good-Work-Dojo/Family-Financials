// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { checkLabel, checkText } from "./checkLabels";

const ACCOUNTS = [{ id: "demo_checking_0101", name: "Joint Checking ...0101" }, { id: "demo_mortgage_0707", name: "Home Mortgage" }];

describe("checkText - quality checks in words, never a bare code", () => {
  it("lifts the account id out of a per-account check and names the account separately", () => {
    expect(checkText("reconcile.demo_checking_0101.store_vs_files", ACCOUNTS))
      .toEqual({ label: "Feed matches the files", account: "Joint Checking ...0101" });
    expect(checkText("reconcile.demo_checking_0101.ledger_drift", ACCOUNTS).label).toBe("Ledger vs feed balance");
    expect(checkText("simplefin.stale.demo_mortgage_0707", ACCOUNTS)).toEqual({ label: "Bank feed reported no balance", account: "Home Mortgage" });
    expect(checkText("coverage.demo_checking_0101", ACCOUNTS).label).toBe("Transaction coverage");
    expect(checkText("demo_checking_0101.balance_rollforward", ACCOUNTS).label).toBe("Balance rolls forward");   // the id is a prefix here
  });
  it("labels the checks at the feed cutover: statement rows dropped or kept, a gap, rows dropped with nothing to replace them", () => {
    const own = [{ id: "demo_checking_0000", name: "Sample Checking" }];
    // one check reports statement rows dropped at the cutover and rows kept while the feed has no data yet
    expect(checkText("simplefin.csv_clip.demo_checking_0000", own))
      .toEqual({ label: "Statement rows at the feed cutover", account: "Sample Checking" });
    expect(checkText("simplefin.cutover_gap.demo_checking_0000", own))
      .toEqual({ label: "Gap at the feed cutover", account: "Sample Checking" });
    expect(checkText("simplefin.cutover_overlap.demo_checking_0000", own))
      .toEqual({ label: "Overlap at the feed cutover", account: "Sample Checking" });
  });
  it("labels the statement-file checks: a file not read, a file balance not used, running balances, files no account reads", () => {
    expect(checkText("import.demo_checking_0101.SampleBank 2030-01.v2.csv", ACCOUNTS))
      .toEqual({ label: "Could not read SampleBank 2030-01.v2.csv", account: "Joint Checking ...0101" });
    expect(checkLabel("import.demo_checking_0101.statement-2030-01.csv", ACCOUNTS))
      .toBe("Could not read statement-2030-01.csv · Joint Checking ...0101");
    // a file in a subfolder is named by its path in the statements folder, whole
    expect(checkText("import.demo_checking_0101.2030/sub/statement-2030-01.csv", ACCOUNTS))
      .toEqual({ label: "Could not read 2030/sub/statement-2030-01.csv", account: "Joint Checking ...0101" });
    expect(checkText("import.balance_ignored.demo_mortgage_0707", ACCOUNTS))
      .toEqual({ label: "A balance in its file is not used", account: "Home Mortgage" });
    expect(checkText("import.rollforward.demo_checking_0101", ACCOUNTS))
      .toEqual({ label: "Balances in its file add up", account: "Joint Checking ...0101" });
    expect(checkText("source.unmatched", ACCOUNTS)).toEqual({ label: "Files no account reads", account: null });
  });
  it("labels a bank id given to two different rows, descriptions cut, and how an account's sources were joined", () => {
    expect(checkText("import.ofx_conflict.demo_checking_0101", ACCOUNTS))
      .toEqual({ label: "Two different rows share one bank id in its files", account: "Joint Checking ...0101" });
    expect(checkText("import.long_description.demo_checking_0101", ACCOUNTS))
      .toEqual({ label: "Descriptions cut to 500 characters", account: "Joint Checking ...0101" });
    expect(checkLabel("import.sources.demo_checking_0101", ACCOUNTS))
      .toBe("How its sources were joined · Joint Checking ...0101");
    // a file not read is still the file family's, whatever the file is named
    expect(checkText("import.demo_checking_0101.sources.csv", ACCOUNTS))
      .toEqual({ label: "Could not read sources.csv", account: "Joint Checking ...0101" });
  });
  it("labels the rows where two sources meet and the files two sources both claim, never as a file not read", () => {
    expect(checkText("import.sources_seam.demo_checking_0101", ACCOUNTS))
      .toEqual({ label: "Rows where its sources meet", account: "Joint Checking ...0101" });
    expect(checkText("import.matched_twice.demo_checking_0101", ACCOUNTS))
      .toEqual({ label: "Files two of its sources both claim", account: "Joint Checking ...0101" });
    expect(checkLabel("import.matched_twice.demo_mortgage_0707", ACCOUNTS)).toBe("Files two of its sources both claim · Home Mortgage");
  });
  it("a file not read belongs to the account its name starts with, whatever other id the file name holds", () => {
    const name = "import.demo_checking_0101.demo_mortgage_0707.csv";
    for (const accounts of [ACCOUNTS, [...ACCOUNTS].reverse()]) {
      expect(checkText(name, accounts)).toEqual({ label: "Could not read demo_mortgage_0707.csv", account: "Joint Checking ...0101" });
    }
    expect(checkText("import.nobody.statement.csv", ACCOUNTS).account, "an id not in the registry names no account").toBeNull();
  });
  it("a household with no bank feed: the check of the stored rows names only the files", () => {
    expect(checkText("reconcile.demo_checking_0101.store_vs_files", ACCOUNTS, false))
      .toEqual({ label: "Stored rows match the files", account: "Joint Checking ...0101" });
    expect(checkLabel("reconcile.demo_checking_0101.store_vs_files", ACCOUNTS, false))
      .toBe("Stored rows match the files · Joint Checking ...0101");
    expect(checkText("coverage.demo_checking_0101", ACCOUNTS, false).label, "a label naming no feed is the same").toBe("Transaction coverage");
  });
  it("on an account's own page the id is one account, so the label reads the same without the account", () => {
    const own = [{ id: "demo_checking_0101", name: "x" }];
    expect(checkText("reconcile.demo_checking_0101.ledger_drift", own).label).toBe("Ledger vs feed balance");
  });
  it("labels the run-wide checks and the families whose tail names the thing", () => {
    expect(checkText("splits.sum_identity").label).toBe("Split parts add up to their purchase");
    expect(checkText("simplefin.ingested").label).toBe("Bank feed ingested");
    expect(checkText("learned_rules.Netflix").label).toBe("Learned rule: Netflix");
    expect(checkText("overrides.entry_3").label).toBe("Override entry 3");
    expect(checkText("source.demo_checking_0101.csv").label).toBe("Source: demo_checking_0101.csv");
    expect(checkText("simplefin.unmapped.abc-123").label).toBe("Unmapped bank account");
    expect(checkText("manual_assets.entry").label).toBe("A hand-entered asset left out");
    expect(checkText("manual_assets.debt_sign").label).toBe("A debt stated above zero");
    expect(checkText("manual_assets.load").label).toBe("Hand-entered assets not read");
  });
  it("a check nobody labelled yet is humanized, not shown as a code", () => {
    expect(checkText("answers.some_new_thing").label).toBe("Answers · some new thing");
    expect(checkText("brand_new").label).toBe("Brand new");
    expect(checkText("reconcile.demo_checking_0101.brand_new", ACCOUNTS).label).toBe("Reconcile · demo checking 0101 · brand new");   // an unknown kind keeps the whole name, spelled out
  });
  it("an id that is not one of the known accounts leaves the name whole", () => {
    expect(checkText("reconcile.nobody.ledger_drift", ACCOUNTS)).toEqual({ label: "Reconcile · nobody · ledger drift", account: null });
  });
  it("no label contains an underscore or a dotted code", () => {
    for (const n of ["reconcile.demo_checking_0101.store_vs_files", "simplefin.balance_stale.demo_mortgage_0707", "answers.on_split_parent",
      "categorization.uncategorized_share", "overrides.on_split_parent", "weird_name.thing"]) {
      const { label } = checkText(n, ACCOUNTS);
      expect(label, n).not.toMatch(/_|\w\.\w/);
    }
  });
});

describe("checkLabel - a row of a list across accounts", () => {
  it("is the words, then the account after a middle dot; a run-wide check is the words alone", () => {
    expect(checkLabel("reconcile.demo_checking_0101.ledger_drift", ACCOUNTS)).toBe("Ledger vs feed balance · Joint Checking ...0101");
    expect(checkLabel("splits.sum_identity", ACCOUNTS)).toBe("Split parts add up to their purchase");
  });
});
