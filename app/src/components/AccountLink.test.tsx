// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import AccountLink, { ACCOUNT_LINK, ACCOUNT_LINK_QUIET } from "./AccountLink";

afterEach(cleanup);

describe("AccountLink", () => {
  it("links /accounts/:id with a resting link style (colour and underline), not only a hover", () => {
    render(<MemoryRouter><AccountLink id="chk_1">Everyday Checking</AccountLink></MemoryRouter>);
    const a = screen.getByRole("link", { name: "Everyday Checking" });
    expect(a.getAttribute("href")).toBe("/accounts/chk_1");
    const classes = a.className.split(/\s+/);
    expect(classes).toContain("text-blue-400");
    expect(classes, "an underline at rest (not just underline-offset)").toContain("underline");
    for (const cls of ACCOUNT_LINK.split(" ")) expect(classes).toContain(cls);
  });
  it('appearance="quiet": secondary text at rest - no blue, no underline - and the link look only on hover or keyboard focus', () => {
    render(<MemoryRouter><AccountLink id="chk_1" appearance="quiet">Everyday Checking</AccountLink></MemoryRouter>);
    const a = screen.getByRole("link", { name: "Everyday Checking" });
    const classes = a.className.split(/\s+/);
    for (const cls of ACCOUNT_LINK_QUIET.split(" ")) expect(classes).toContain(cls);
    expect(classes).toContain("text-zinc-400");
    expect(classes).not.toContain("underline");
    expect(classes).not.toContain("text-blue-400");
    expect(classes).toEqual(expect.arrayContaining(["hover:underline", "hover:text-blue-400", "focus-visible:underline", "focus-visible:text-blue-400"]));
    expect(a.hasAttribute("tabindex"), "quiet is a look, not a tab-order change").toBe(false);
  });
  it("escapes an id that is not a path segment, and keeps a caller's classes and click handler", () => {
    const onClick = vi.fn();
    render(<MemoryRouter><AccountLink id="a b/c" className="font-bold" onClick={onClick}>X</AccountLink></MemoryRouter>);
    const a = screen.getByRole("link", { name: "X" });
    expect(a.getAttribute("href")).toBe("/accounts/a%20b%2Fc");
    expect(a.className).toContain("font-bold");
    fireEvent.click(a);
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
