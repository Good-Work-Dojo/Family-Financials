// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { isPrivate, setPrivate } from "../../lib/privacy";
import PrivacyCard from "./PrivacyCard";

afterEach(() => { cleanup(); setPrivate(false); });

describe("PrivacyCard (Data → card-privacy)", () => {
  it("is the capability's card, with a toggle that reads Hide amounts, unpressed", () => {
    render(<PrivacyCard />);
    expect(screen.getByTestId("card-privacy")).toBeDefined();
    const b = screen.getByRole("button", { name: /Hide amounts/ });
    expect(b.getAttribute("aria-pressed")).toBe("false");
    expect(b.className).toMatch(/min-h-\[40px\]/);
  });
  it("flips the flag, reads Show amounts while pressed, and follows a change made elsewhere (the hotkey)", () => {
    render(<PrivacyCard />);
    fireEvent.click(screen.getByRole("button", { name: /Hide amounts/ }));
    expect(isPrivate()).toBe(true);
    const b = screen.getByRole("button", { name: /Show amounts/ });
    expect(b.getAttribute("aria-pressed")).toBe("true");
    act(() => setPrivate(false));
    expect(screen.getByRole("button", { name: /Hide amounts/ }).getAttribute("aria-pressed")).toBe("false");
  });
  it("says what it hides, what stays, the hotkey, and that CSV exports are never masked", () => {
    render(<PrivacyCard />);
    const text = screen.getByTestId("card-privacy").textContent ?? "";
    expect(text).toMatch(/every dollar amount/);
    expect(text).toMatch(/Names, dates, counts and percentages stay/);
    expect(text).toMatch(/Alt\+Shift\+P/);
    expect(text).toMatch(/CSV exports are never masked/);
  });
});
