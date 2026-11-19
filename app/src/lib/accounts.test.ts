// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import type { AccountMeta } from "../data/types";
import { DEFAULT_CLASS_BY_KIND, defaultClass, isCashLike, isInvestmentAccount } from "./accounts";

const KINDS = Object.keys(DEFAULT_CLASS_BY_KIND) as AccountMeta["kind"][];
/** an account as the maps that reach isCashLike carry it: a kind and a class */
const account = (kind: string, asset_class: string) => ({ kind, asset_class });

describe("isInvestmentAccount", () => {
  it("is a brokerage whose money is not cash: a brokerage's cash account and a savings account are not", () => {
    expect(isInvestmentAccount(account("brokerage", "Stocks/ETF"))).toBe(true);
    expect(isInvestmentAccount(account("brokerage", "Retirement"))).toBe(true);
    expect(isInvestmentAccount(account("brokerage", "Cash"))).toBe(false);
    expect(isInvestmentAccount(account("savings", "529"))).toBe(false);
  });
});

describe("isCashLike", () => {
  it("treats checking and savings on the Cash class as cash", () => {
    expect(isCashLike(account("checking", "Cash"))).toBe(true);
    expect(isCashLike(account("savings", "Cash"))).toBe(true);
  });

  it("treats a cash-classed asset account as cash (a brokerage's cash account)", () => {
    expect(isCashLike(account("brokerage", "Cash"))).toBe(true);
  });

  it("leaves real investment/liability accounts out", () => {
    expect(isCashLike(account("brokerage", "Retirement"))).toBe(false);
    expect(isCashLike(account("loan", "Mortgage"))).toBe(false);
  });

  it("decides by the class alone: an account of a cash kind that states another class is not cash", () => {
    expect(isCashLike(account("savings", "529"))).toBe(false);
    expect(isCashLike(account("checking", "Retirement"))).toBe(false);
    expect(isCashLike(account("savings", "Other Debt"))).toBe(false);
  });

  it("reads an account whose class comes from its kind as that class: only checking and savings are cash", () => {
    expect(KINDS.filter((kind) => isCashLike(account(kind, defaultClass(kind, false))))).toEqual(["checking", "savings"]);
    expect(isCashLike(account("loan", defaultClass("loan", true)))).toBe(false);
  });
});

describe("defaultClass", () => {
  it("is the engine's class for each kind, and Mortgage for the mortgage loan alone", () => {
    expect(KINDS.map((k) => [k, defaultClass(k, false)])).toEqual([
      ["checking", "Cash"], ["savings", "Cash"], ["brokerage", "Stocks/ETF"], ["credit-card", "Other Debt"],
      ["loan", "Other Debt"], ["wallet", "Crypto"]]);
    expect(defaultClass("loan", true)).toBe("Mortgage");
    expect(defaultClass("checking", true), "only a loan is ever the mortgage").toBe("Cash");
  });
});
