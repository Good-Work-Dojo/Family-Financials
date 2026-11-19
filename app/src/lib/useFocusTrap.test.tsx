// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useFocusTrap } from "./useFocusTrap";

function Trap({ onClose }: { onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, onClose);
  return (
    <div ref={ref} tabIndex={-1}>
      <button>first</button>
      <button>last</button>
    </div>
  );
}
afterEach(cleanup);

describe("useFocusTrap", () => {
  it("focuses the first control, wraps Tab and Shift+Tab, closes on Escape, restores focus", () => {
    const outside = document.createElement("button");
    document.body.appendChild(outside);
    outside.focus();
    const onClose = vi.fn();
    const { unmount } = render(<Trap onClose={onClose} />);
    const first = screen.getByText("first"), last = screen.getByText("last");
    expect(document.activeElement).toBe(first);
    last.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(first);
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    unmount();
    expect(document.activeElement).toBe(outside);
    outside.remove();
  });

  it("pulls focus back inside when Tab is pressed from outside the dialog", () => {
    const outside = document.createElement("button");
    document.body.appendChild(outside);
    render(<Trap onClose={() => {}} />);
    outside.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(screen.getByText("first"));
    outside.focus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(screen.getByText("last"));
    outside.remove();
  });

  it("an inline onClose never re-arms the effect (focus is not stolen on re-render)", () => {
    const { rerender } = render(<Trap onClose={() => {}} />);
    screen.getByText("last").focus();
    rerender(<Trap onClose={() => {}} />);
    expect(document.activeElement).toBe(screen.getByText("last"));
  });
});

function Trap2({ onClose, label }: { onClose: () => void; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, onClose);
  return <div ref={ref} tabIndex={-1}><button>{label}</button></div>;
}
describe("useFocusTrap stack", () => {
  it("stacked dialogs: Escape closes only the topmost trap; after it unmounts the one below answers again", () => {
    const closeA = vi.fn(), closeB = vi.fn();
    render(<Trap2 onClose={closeA} label="a" />);
    const b = render(<Trap2 onClose={closeB} label="b" />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(closeB).toHaveBeenCalledTimes(1);
    expect(closeA).not.toHaveBeenCalled();
    b.unmount();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(closeA).toHaveBeenCalledTimes(1);
  });
  it("stacked dialogs: Tab is handled by the topmost trap only (the one below never steals focus back)", () => {
    render(<Trap2 onClose={() => {}} label="a" />);
    render(<Trap2 onClose={() => {}} label="b" />);
    const a = screen.getByText("a"), b = screen.getByText("b");
    expect(document.activeElement).toBe(b);
    const stolen = vi.spyOn(a, "focus");                  // the lower trap would drag focus onto its own control
    fireEvent.keyDown(document, { key: "Tab" });          // b is the only control of the top trap: Tab wraps onto itself
    expect(stolen).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(b);
  });
  it("a trap that unmounts out of order leaves the others in place (the stack removes the right entry)", () => {
    const closeA = vi.fn(), closeB = vi.fn(), closeC = vi.fn();
    const a = render(<Trap2 onClose={closeA} label="a" />);
    render(<Trap2 onClose={closeB} label="b" />);
    const c = render(<Trap2 onClose={closeC} label="c" />);
    a.unmount();                                          // the bottom one goes first
    fireEvent.keyDown(document, { key: "Escape" });
    expect(closeC).toHaveBeenCalledTimes(1);
    c.unmount();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(closeB).toHaveBeenCalledTimes(1);
    expect(closeA).not.toHaveBeenCalled();
  });
  it("a lower trap that unmounts first does not drag focus back to its opener over the dialog still open", () => {
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();
    const a = render(<Trap2 onClose={() => {}} label="a" />);
    render(<Trap2 onClose={() => {}} label="b" />);
    expect(document.activeElement).toBe(screen.getByText("b"));
    a.unmount();                                          // the bottom one goes first: its opener is `opener`
    expect(document.activeElement, "focus stays in the dialog that is still open").toBe(screen.getByText("b"));
    opener.remove();
  });
  it("stacked dialogs unwind focus one opener at a time: the top gives it back to the one below, which gives it back to the page", () => {
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();
    const a = render(<Trap2 onClose={() => {}} label="a" />);
    const b = render(<Trap2 onClose={() => {}} label="b" />);
    b.unmount();
    expect(document.activeElement, "the top gave focus back into the dialog below").toBe(screen.getByText("a"));
    a.unmount();
    expect(document.activeElement, "the last one gave it back to the page").toBe(opener);
    opener.remove();
  });
  it("initialFocus: 'container' puts focus on the dialog box itself, not its first control", () => {
    function Box() { const ref = useRef<HTMLDivElement>(null); useFocusTrap(ref, () => {}, { initialFocus: "container" }); return <div ref={ref} tabIndex={-1} data-testid="box"><button>first</button></div>; }
    render(<Box />);
    expect(document.activeElement).toBe(screen.getByTestId("box"));
  });
});
