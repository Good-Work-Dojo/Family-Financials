// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { isPrivate, setPrivate } from "../lib/privacy";
import HouseholdName from "./HouseholdName";

afterEach(() => { cleanup(); setPrivate(false); });

describe("HouseholdName", () => {
  it("prints the household's name as plain text, cut with an ellipsis when long, its full text in title", () => {
    const long = "A household whose name runs far past the width of the sidebar header";
    render(<HouseholdName name={long} />);
    const el = screen.getByTestId("household-name");
    expect(el.textContent).toBe(long);
    expect(el.getAttribute("title")).toBe(long);
    expect(el.className.split(/\s+/)).toContain("truncate");
    expect(el.querySelector("a, button, [tabindex]"), "plain text, no tab stop").toBeNull();
  });

  it("trims the name it is given", () => {
    render(<HouseholdName name="  The Example household " />);
    expect(screen.getByTestId("household-name").textContent).toBe("The Example household");
    expect(screen.getByTestId("household-name").getAttribute("title")).toBe("The Example household");
  });

  it("draws nothing for an empty or blank name: no placeholder", () => {
    for (const name of ["", "   "]) {
      const { container } = render(<HouseholdName name={name} />);
      expect(container.innerHTML, JSON.stringify(name)).toBe("");
      cleanup();
    }
  });

  it("stays readable in privacy mode: a name is not an amount", () => {
    setPrivate(true);
    expect(isPrivate()).toBe(true);
    render(<HouseholdName name="The Example household" />);
    expect(screen.getByTestId("household-name").textContent).toBe("The Example household");
  });
});
