// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { toCsv } from "./csv";

describe("toCsv (RFC 4180)", () => {
  it("quotes fields containing commas, quotes, newlines", () => {
    const csv = toCsv([["a,b", 'say "hi"', "line1\nline2", 42]], ["c1", "c2", "c3", "c4"]);
    expect(csv).toBe('c1,c2,c3,c4\r\n"a,b","say ""hi""","line1\nline2",42\r\n');
  });

  it("empty row set yields header only", () => {
    expect(toCsv([], ["a", "b"])).toBe("a,b\r\n");
  });

  it("null/undefined become empty fields", () => {
    expect(toCsv([[null, undefined, 0]], ["x", "y", "z"])).toBe("x,y,z\r\n,,0\r\n");
  });
});

describe("toCsv: a text cell a spreadsheet would run as a formula is written as text", () => {
  // the six characters a spreadsheet reads as the start of a formula (or skips before one): each text cell that begins
  // with one gets a leading apostrophe, which spreadsheets take as "this is text" and do not show
  it.each([
    ["=HYPERLINK(\"https://example.net\",\"x\")", "\"'=HYPERLINK(\"\"https://example.net\"\",\"\"x\"\")\""],
    ["-5 off coupon", "'-5 off coupon"],
    ["+1 (555) 010-0000", "'+1 (555) 010-0000"],
    ["@handle", "'@handle"],
    ["\tSUM(A1:A2)", "'\tSUM(A1:A2)"],
    ["\rcmd", "\"'\rcmd\""],
  ])("%j", (text, written) => {
    expect(toCsv([[text]], ["description"])).toBe(`description\r\n${written}\r\n`);
  });

  it("a number stays a number, a negative amount included, whatever its sign", () => {
    expect(toCsv([[-12.5, 12.5, -0.01, 0]], ["a", "b", "c", "d"])).toBe("a,b,c,d\r\n-12.5,12.5,-0.01,0\r\n");
  });

  it("decides by the cell's type, not its text: the same characters as text are neutralised, as a number are not", () => {
    expect(toCsv([["-12.50", -12.5]], ["as text", "as number"])).toBe("as text,as number\r\n'-12.50,-12.5\r\n");
  });

  it("text that only contains one of the characters later on is written as it is", () => {
    expect(toCsv([["Coffee = good", "a-b", "x@example.net", "1+1"]], ["a", "b", "c", "d"]))
      .toBe("a,b,c,d\r\nCoffee = good,a-b,x@example.net,1+1\r\n");
  });

  it("a header cell is text too", () => {
    expect(toCsv([[1]], ["=1+1"])).toBe("'=1+1\r\n1\r\n");
  });

  it("a cell holding a semicolon is quoted: where `;` separates cells it cannot start a second one with a formula", () => {
    expect(toCsv([["x;=HYPERLINK(\"https://example.net\")", "groceries;household"]], ["description", "tags"]))
      .toBe("description,tags\r\n\"x;=HYPERLINK(\"\"https://example.net\"\")\",\"groceries;household\"\r\n");
  });
});
