// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { OpenRoutesProvider, type RouteOpen } from "../lib/modules";
import GatedLink, { WhileShown } from "./GatedLink";

afterEach(cleanup);

const mount = (open: RouteOpen | null, whenOff?: ReactNode) => render(
  <MemoryRouter>
    <p data-testid="line">
      The what-if lives in{" "}
      {open === null
        ? <GatedLink to="/planning/home?card=x" className="link" whenOff={whenOff}>Planning → House</GatedLink>
        : <OpenRoutesProvider value={open}>
          <GatedLink to="/planning/home?card=x" className="link" whenOff={whenOff}>Planning → House</GatedLink>
        </OpenRoutesProvider>}.
    </p>
  </MemoryRouter>);

describe("GatedLink", () => {
  it("is a link, with its class and the whole address, while the page it leads to shows", () => {
    mount(() => true);
    const link = screen.getByRole("link", { name: "Planning → House" });
    expect(link.getAttribute("href")).toBe("/planning/home?card=x");
    expect(link.className).toBe("link");
  });
  it("is the same words as plain text while the page is off: never a link, never hidden", () => {
    const asked: string[] = [];
    mount((path) => { asked.push(path); return false; });
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByTestId("line").textContent).toBe("The what-if lives in Planning → House.");
    expect(asked, "it asks about its own address").toEqual(["/planning/home?card=x"]);
    expect(screen.getByTestId("line").querySelector(".link"), "the link's style is not carried").toBeNull();
  });
  it("shows `whenOff` instead while the page is off: nothing at all, or other words", () => {
    mount(() => false, null);
    expect(screen.getByTestId("line").textContent).toBe("The what-if lives in .");
    cleanup();
    mount(() => false, <em>elsewhere</em>);
    expect(screen.getByTestId("line").textContent).toBe("The what-if lives in elsewhere.");
    cleanup();
    mount(() => true, null);
    expect(screen.getByRole("link", { name: "Planning → House" }), "whenOff plays no part while the page shows").toBeDefined();
  });
  it("outside the shell, with no answer given, every page shows", () => {
    mount(null);
    expect(screen.getByRole("link", { name: "Planning → House" })).toBeDefined();
  });
});

describe("WhileShown", () => {
  const mountWhile = (open: boolean) => render(
    <OpenRoutesProvider value={(path) => open || !path.startsWith("/planning/home")}>
      <p data-testid="line">
        <WhileShown to="/planning/home">The what-if lives on its own page. </WhileShown>
        <WhileShown to="/planning">Projections is always there.</WhileShown>
      </p>
    </OpenRoutesProvider>);
  it("renders its words only while their page shows", () => {
    mountWhile(true);
    expect(screen.getByTestId("line").textContent).toBe("The what-if lives on its own page. Projections is always there.");
    cleanup();
    mountWhile(false);
    expect(screen.getByTestId("line").textContent).toBe("Projections is always there.");
  });
});
