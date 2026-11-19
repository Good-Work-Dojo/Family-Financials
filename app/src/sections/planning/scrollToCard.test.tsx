// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Card } from "../../components/ui";
import { cardPath, SCROLL_TARGETS, scrollToCard, useScrollToCardParam } from "../../lib/scrollTargets";

// The `?card=` key (lib/scrollTargets.ts): a hash link cannot target an in-page anchor under HashRouter, so a page
// reads the key itself and, on arrival, scrolls to the card and moves focus to its heading.
function Page() {
  useScrollToCardParam();
  return <div>
    <Card id={SCROLL_TARGETS.runway} testId="runway" title="Emergency fund">a</Card>
    <Card id={SCROLL_TARGETS.privacy} testId="privacy" title="Privacy mode">b</Card>
    <div id="not-a-target" data-testid="other" />
  </div>;
}
const visit = (search: string) => render(<MemoryRouter initialEntries={[`/planning${search}`]}><Page /></MemoryRouter>);

const scrolled = vi.fn();
beforeEach(() => {
  scrolled.mockReset();
  // jsdom has no layout engine: record which element was asked to scroll
  Element.prototype.scrollIntoView = function (this: Element) { scrolled(this.id); };
});
afterEach(cleanup);

describe("useScrollToCardParam", () => {
  it("scrolls to the card named by ?card= and moves focus to its heading", () => {
    visit(`?card=${SCROLL_TARGETS.runway}`);
    expect(scrolled.mock.calls).toEqual([[SCROLL_TARGETS.runway]]);
    expect(document.activeElement?.textContent).toBe("Emergency fund");
    expect(document.activeElement?.tagName).toBe("H2");
  });
  it("lands on any known card the page shows", () => {
    visit(`?card=${SCROLL_TARGETS.privacy}`);
    expect(scrolled.mock.calls).toEqual([[SCROLL_TARGETS.privacy]]);
    expect(document.activeElement?.textContent).toBe("Privacy mode");
  });
  it("ignores a value that is not a scroll target: no other element id may be named by the URL", () => {
    visit("?card=not-a-target");
    expect(scrolled).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(document.body);
  });
  it("does nothing without the key, and nothing when the named card is not on the page", () => {
    visit("");
    visit("?other=1");
    expect(scrolled).not.toHaveBeenCalled();
    cleanup();
    render(<MemoryRouter initialEntries={[`/planning?card=${SCROLL_TARGETS.budget}`]}><Gated /></MemoryRouter>);
    expect(scrolled).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(document.body);
  });
});

describe("scrollToCard", () => {
  it("scrolls without moving focus unless asked: a KPI's click keeps its own focus", () => {
    render(<MemoryRouter><Page /></MemoryRouter>);
    scrollToCard(SCROLL_TARGETS.runway);
    expect(scrolled.mock.calls).toEqual([[SCROLL_TARGETS.runway]]);
    expect(document.activeElement).toBe(document.body);
    scrollToCard(SCROLL_TARGETS.runway, { focus: true });
    expect(document.activeElement?.textContent).toBe("Emergency fund");
  });
});

describe("a card that can be landed on", () => {
  it("clears the sticky phone header and has a heading focusable by script only; a card without an id has neither", () => {
    render(<><Card id="x" testId="with" title="With">a</Card><Card testId="without" title="Without">b</Card></>);
    const withId = document.querySelector('[data-testid="with"]')!;
    expect(withId.className.split(/\s+/)).toContain("scroll-mt-14");
    expect(withId.querySelector("h2")!.getAttribute("tabindex")).toBe("-1");
    const without = document.querySelector('[data-testid="without"]')!;
    expect(without.className.split(/\s+/)).not.toContain("scroll-mt-14");
    expect(without.querySelector("h2")!.hasAttribute("tabindex")).toBe(false);
  });
  it("is opened by a path that names its route and its card", () => {
    expect(cardPath("/data", SCROLL_TARGETS.privacy)).toBe("/data?card=privacy-mode");
  });
});

/** a page whose card is gated off (its data is missing): the arrival is a quiet no-op */
function Gated() {
  useScrollToCardParam();
  return <div />;
}
