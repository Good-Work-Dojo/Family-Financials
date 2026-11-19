// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { TableScroll } from "./ui";

afterEach(cleanup);

/** gives the scroller the box a browser would lay it out in, then tells it it scrolled (jsdom lays nothing out) */
function layOut(el: HTMLElement, box: { clientWidth: number; scrollWidth: number; scrollLeft: number }) {
  for (const [k, v] of Object.entries(box)) Object.defineProperty(el, k, { value: v, configurable: true });
  act(() => { el.dispatchEvent(new Event("scroll")); });
}

const table = (
  <table>
    <tbody><tr><th scope="row">Groceries</th><td>1</td><td>2</td></tr></tbody>
  </table>
);

describe("TableScroll", () => {
  it("a table that fits is a plain box: no region, no tab stop, no cue", () => {
    render(<TableScroll label="Budget table">{table}</TableScroll>);
    const box = screen.getByTestId("table-scroll");
    layOut(box, { clientWidth: 358, scrollWidth: 358, scrollLeft: 0 });
    expect(box.getAttribute("role")).toBeNull();
    expect(box.hasAttribute("tabindex")).toBe(false);
    expect(screen.queryByTestId("table-scroll-cue")).toBeNull();
  });

  it("a wider table is a labelled region a keyboard can reach, with a cue while more lies to the right", () => {
    render(<TableScroll label="Report table">{table}</TableScroll>);
    const box = screen.getByTestId("table-scroll");
    layOut(box, { clientWidth: 358, scrollWidth: 520, scrollLeft: 0 });
    expect(screen.getByRole("region", { name: "Report table" })).toBe(box);
    expect(box.tabIndex).toBe(0);
    const cue = screen.getByTestId("table-scroll-cue");
    expect(cue.textContent).toBe("scroll →");
    expect(cue.getAttribute("aria-hidden")).toBe("true");
    expect(cue.className.split(/\s+/)).not.toContain("invisible");
    // at the end the cue goes and its line stays, so nothing under it moves
    layOut(box, { clientWidth: 358, scrollWidth: 520, scrollLeft: 162 });
    expect(screen.getByTestId("table-scroll-cue").className.split(/\s+/)).toContain("invisible");
    // and back again
    layOut(box, { clientWidth: 358, scrollWidth: 520, scrollLeft: 40 });
    expect(screen.getByTestId("table-scroll-cue").className.split(/\s+/)).not.toContain("invisible");
  });

  it("stickyFirst keeps each row's first cell at the scroller's left edge on an opaque fill", () => {
    render(<TableScroll label="P&L" stickyFirst>{table}</TableScroll>);
    const cls = screen.getByTestId("table-scroll").className.split(/\s+/);
    expect(cls).toEqual(expect.arrayContaining(["[&_tr>:first-child]:sticky", "[&_tr>:first-child]:left-0"]));
    expect(cls.some((c) => c.startsWith("[&_tr>:first-child]:bg-"))).toBe(true);
    render(<TableScroll label="Sources">{table}</TableScroll>);
    expect(screen.getAllByTestId("table-scroll")[1].className).not.toContain("sticky");
  });
});
