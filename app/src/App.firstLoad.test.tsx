// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
// What the app's first load shows when the exports are not there to read: with no manifest at all (the server has none,
// or answers with a page instead) the first-run page with every step still to take, outside the shell; any other
// failure (a redirect that ends on a page among them) the loader's error screen, as before.
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.location.hash = ""; });

/** every request answered by `manifest` for manifest.json and a 404 for anything else (the other exports, the store) */
const serve = (manifest: () => Response) => vi.stubGlobal("fetch", async (url: string) =>
  (String(url).replace(/^\.\//, "") === "manifest.json" ? manifest() : new Response("Not Found", { status: 404 })));

describe("the first load with nothing built", () => {
  for (const [what, answer] of [
    ["the server has no manifest (404)", () => new Response("Not Found", { status: 404 })],
    ["the server answers with a page where the manifest should be", () =>
      new Response("<!doctype html><html><head><title>app</title></head><body></body></html>", { status: 200,
        headers: { "Content-Type": "text/html" } })],
  ] as const) {
    it(`${what}: the first-run page, all three steps to take, and no error`, async () => {
      serve(answer);
      render(<App />);
      const page = await screen.findByTestId("first-run");
      expect(page.getAttribute("data-first-run")).toBe("nothing-built");
      expect(screen.getAllByRole("heading", { level: 1 }).map((h) => h.textContent)).toEqual(["Set up your household"]);
      const steps = within(screen.getByTestId("card-first-run-steps")).getByRole("list");
      expect(steps.querySelectorAll("li")).toHaveLength(3);
      expect(steps.textContent, "no step is marked done").not.toMatch(/Done|✓/);
      // not the error screen, nor its styling
      expect(screen.queryByRole("alert")).toBeNull();
      expect(document.body.textContent).not.toMatch(/Couldn't load data/);
      expect(document.body.querySelector('[class*="red-"]'), "no error colours").toBeNull();
      // outside the shell: no sidebar and no top bar to build from data that is not there
      expect(screen.queryByRole("navigation", { name: "Main" })).toBeNull();
      expect(screen.getByRole("button", { name: "Check now" })).toBeDefined();
      expect(document.title).toBe("Set up · Family Financials");
    });
  }

  for (const [what, answer] of [
    ["a 500", () => new Response("{}", { status: 500 })],
    ["a manifest that does not parse", () => new Response("{\"schema_version\": 20, \"generated_at\"", { status: 200 })],
    // a login or portal page in front of the dashboard: a household with data behind it is never told no ingest has run
    ["a redirect that ends on a page", () => Object.defineProperty(
      new Response("<!doctype html><html><body>Sign in</body></html>", { status: 200 }), "redirected", { value: true })],
  ] as const) {
    it(`${what}: the loader's error screen, unchanged`, async () => {
      serve(answer);
      render(<App />);
      const alert = await screen.findByRole("alert");
      expect(alert.textContent).toMatch(/^Couldn't load data/);
      expect(screen.getByRole("button", { name: "Try again" })).toBeDefined();
      expect(screen.queryByTestId("first-run")).toBeNull();
    });
  }
});
