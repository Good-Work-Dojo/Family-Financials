// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Link, MemoryRouter, Routes, useLocation, useParams } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// the module-off page reads the household's modules: here every module is off
vi.mock("../data/useModules", () => ({
  useModules: () => ({ modules: { home_buying: false }, facts: { mortgage: false }, setModule: () => {} }),
}));
import { type PageDef, pageRoute } from "../App";
import HubShell from "./HubShell";

beforeEach(() => { vi.spyOn(console, "error").mockImplementation(() => {}); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function Boom(): never { throw new Error("tab exploded"); }
/** throws for one id: the same `:id` route element serves a good and a bad param */
function Account() {
  const { id } = useParams();
  if (id === "bad") throw new Error("bad account id");
  return <div>account {id}</div>;
}

const def: PageDef = {
  to: "/h", label: "Hub", icon: "H",
  hub: {
    indexLabel: "Index", indexKeepSearch: true,
    index: () => <div>index content</div>,
    tabs: [
      { path: "keep", label: "Keep", icon: "K", keepSearch: true, page: () => <div>keep content</div> },
      { path: "pick", label: "Pick", icon: "P", keepSearch: ["a", "b"], page: () => <div>pick content</div> },
      { path: "boom", label: "Boom", icon: "B", page: Boom },
    ],
    extra: [{ path: ":id", page: Account }],
  },
};

/** a hub whose INDEX element throws (the hub's own route, not a tab) */
const boomIndexDef: PageDef = {
  to: "/h", label: "Hub", icon: "H",
  hub: {
    indexLabel: "Index", index: Boom,
    tabs: [{ path: "keep", label: "Keep", icon: "K", page: () => <div>keep content</div> }],
  },
};

/** the production wiring (App.tsx Shell): the real pageRoute with the live pathname and the routes `closed` */
function Tree({ page, closed = [] }: { page: PageDef; closed?: string[] }) {
  const { pathname, state } = useLocation();
  return (
    <>
      <Routes>{pageRoute(page, pathname, null, closed)}</Routes>
      <Link to="/h/good">go good</Link>
      <output data-testid="state">{JSON.stringify(state)}</output>
    </>
  );
}
const renderAt = (initial: string, page: PageDef = def) =>
  render(<MemoryRouter initialEntries={[initial]}><Tree page={page} /></MemoryRouter>);

describe("HubShell's heading on a phone", () => {
  // jsdom applies no CSS, so the phone fold is pinned as the classes that make it: sr-only below md, visible from md up
  it("is screen-reader-only below md (the top bar names the place) and visible from md up", () => {
    render(<MemoryRouter initialEntries={["/x"]}><HubShell title="Direct" tabs={[{ to: "/x", label: "One", end: true }]} /></MemoryRouter>);
    const h1 = screen.getByRole("heading", { level: 1, name: "Direct" });
    const cls = h1.className.split(/\s+/);
    expect(cls).toContain("sr-only");
    expect(cls).toContain("md:not-sr-only");
    expect(cls, "not removed from the outline with display:none").not.toContain("hidden");
  });
  it("the header is a flex column, so the hidden h1 leaves no gap above the tab bar", () => {
    render(<MemoryRouter initialEntries={["/x"]}><HubShell title="Direct" tabs={[{ to: "/x", label: "One", end: true }]} /></MemoryRouter>);
    const header = screen.getByRole("heading", { level: 1 }).parentElement!;
    expect(header.className).toContain("flex flex-col gap-4");
    expect(header.className).not.toContain("space-y-");
  });
});

describe("HubShell with pages switched off", () => {
  it("a hub with one tab to show draws no tab bar, and leaves no gap on a phone where its h1 is hidden", () => {
    render(<MemoryRouter initialEntries={["/x"]}><HubShell title="Direct" tabs={[{ to: "/x", label: "One", end: true }]} /></MemoryRouter>);
    expect(screen.queryByRole("navigation", { name: "Direct sections" })).toBeNull();
    expect(screen.queryByRole("link", { name: "One" })).toBeNull();
    const header = screen.getByRole("heading", { level: 1, name: "Direct" }).parentElement!;
    expect(header.className.split(/\s+/)).toContain("md:mb-4");
    expect(header.className.split(/\s+/)).not.toContain("mb-4");
  });
  it("without its heading (the page shown carries its own h1) it keeps a tab bar of two, and draws nothing with one", () => {
    const tabs = [{ to: "/x", label: "One", end: true }, { to: "/x/two", label: "Two" }];
    const r = render(<MemoryRouter initialEntries={["/x/three"]}><HubShell title="Direct" tabs={tabs} heading={false} /></MemoryRouter>);
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
    expect(screen.getByRole("navigation", { name: "Direct sections" })).toBeDefined();
    r.unmount();
    const lone = render(<MemoryRouter><HubShell title="Direct" tabs={tabs.slice(0, 1)} heading={false} /></MemoryRouter>);
    expect(lone.container.querySelector("header")).toBeNull();
  });
  it("pageRoute: a closed tab is left out of the bar and its route shows the module-off page with the tab's name", () => {
    const gated: PageDef = { ...def, hub: { ...def.hub!, tabs: def.hub!.tabs.map((t) =>
      (t.path === "pick" ? { ...t, module: "home_buying" as const } : t)) } };
    render(<MemoryRouter initialEntries={["/h/pick"]}><Tree page={gated} closed={["/h/pick"]} /></MemoryRouter>);
    expect(screen.getByTestId("module-off")).toBeDefined();
    expect(screen.queryByText("pick content")).toBeNull();
    expect(screen.getAllByRole("heading", { level: 1 }).map((h) => h.textContent), "the hub's h1 gives way").toEqual(["Pick"]);
    const bar = screen.getByRole("navigation", { name: "Hub sections" });
    expect([...bar.querySelectorAll("a")].map((a) => a.textContent)).toEqual(["Index", "Keep", "Boom"]);
    cleanup();
    // on the hub's other routes the h1 is back, and the bar still lacks the closed tab
    render(<MemoryRouter initialEntries={["/h/keep"]}><Tree page={gated} closed={["/h/pick"]} /></MemoryRouter>);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Hub");
    expect(screen.queryByRole("link", { name: "Pick" })).toBeNull();
    expect(screen.getByText("keep content")).toBeDefined();
  });
});

describe("HubShell", () => {
  it("renders the title and the tab links it is given", () => {
    render(
      <MemoryRouter initialEntries={["/x"]}>
        <HubShell title="Direct" tabs={[{ to: "/x", label: "One", end: true }, { to: "/x/two", label: "Two" }]} />
      </MemoryRouter>);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Direct");
    expect(screen.getByRole("navigation", { name: "Direct sections" })).toBeDefined();
    expect(screen.getByRole("link", { name: "Two" }).getAttribute("href")).toBe("/x/two");
  });

  it("draws keyboard focus inside the link: the scrolling tab bar would clip an outset outline", () => {
    render(
      <MemoryRouter initialEntries={["/x"]}>
        <HubShell title="Direct" tabs={[{ to: "/x", label: "One", end: true }, { to: "/x/two", label: "Two" }]} />
      </MemoryRouter>);
    for (const name of ["One", "Two"]) {
      const cls = screen.getByRole("link", { name }).className.split(/\s+/);
      expect(cls).toContain("focus-visible:outline-2");
      expect(cls).toContain("focus-visible:-outline-offset-2");
    }
  });

  it("a crashing tab renders the alert card while the h1 and the tab links stay usable", () => {
    renderAt("/h/boom");
    expect(screen.getByRole("alert").textContent).toMatch(/tab exploded/);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Hub");
    expect(screen.getByRole("link", { name: "Index" })).toBeDefined();
  });
  it("switching tabs clears the crashed tab's error", () => {
    renderAt("/h/boom");
    fireEvent.click(screen.getByRole("link", { name: "Index" }));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("index content")).toBeDefined();
  });
  it("a crashing hub INDEX renders the alert card while the h1 and the sibling tab links stay usable", () => {
    renderAt("/h", boomIndexDef);
    expect(screen.getByRole("alert").textContent).toMatch(/tab exploded/);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Hub");
    expect(screen.getByRole("link", { name: "Index" })).toBeDefined();
    expect(screen.getByRole("link", { name: "Keep" })).toBeDefined();
    // the way out works: the sibling tab renders and the boundary card is gone
    fireEvent.click(screen.getByRole("link", { name: "Keep" }));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("keep content")).toBeDefined();
  });
  it("a param change on the SAME route clears the error without remounting the boundary", () => {
    renderAt("/h/bad");
    expect(screen.getByRole("alert").textContent).toMatch(/bad account id/);
    fireEvent.click(screen.getByText("go good"));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("account good")).toBeDefined();
  });

  it("keepSearch tabs carry the current query; the others drop it", () => {
    renderAt("/h?categories=Dining");
    expect(screen.getByRole("link", { name: "Index" }).getAttribute("href")).toBe("/h?categories=Dining");
    expect(screen.getByRole("link", { name: "Keep" }).getAttribute("href")).toBe("/h/keep?categories=Dining");
    expect(screen.getByRole("link", { name: "Boom" }).getAttribute("href")).toBe("/h/boom");
  });
  it("a keepSearch LIST carries only the listed keys, in any order, and nothing when none is present", () => {
    renderAt("/h?win=12m&b=2&a=1&snap=x");
    expect(screen.getByRole("link", { name: "Pick" }).getAttribute("href")).toBe("/h/pick?b=2&a=1");
    // `true` keeps the whole query, as before
    expect(screen.getByRole("link", { name: "Keep" }).getAttribute("href")).toBe("/h/keep?win=12m&b=2&a=1&snap=x");
    cleanup();
    renderAt("/h?win=12m");
    expect(screen.getByRole("link", { name: "Pick" }).getAttribute("href")).toBe("/h/pick");
  });
  it("a tab link marks the handover only when it leaves a tab that shares the query for one that does too", () => {
    // Keep -> Index: both share the query, so the next page is told it arrived with state to keep
    renderAt("/h/keep?a=1");
    fireEvent.click(screen.getByRole("link", { name: "Index" }));
    expect(screen.getByTestId("state").textContent).toBe('{"carriedSearch":true}');
    cleanup();
    // Boom -> Keep: the source shares nothing, so the target is a fresh entry (no marker)
    renderAt("/h/boom");
    fireEvent.click(screen.getByRole("link", { name: "Keep" }));
    expect(screen.getByTestId("state").textContent).toBe("null");
    cleanup();
    // Keep -> Boom: the target shares nothing, so no marker either
    renderAt("/h/keep?a=1");
    fireEvent.click(screen.getByRole("link", { name: "Boom" }));
    expect(screen.getByTestId("state").textContent).toBe("null");
  });
  it("a hub extra is routed but never becomes a tab", () => {
    renderAt("/h/acct-1");
    expect(screen.getByText("account acct-1")).toBeDefined();
    expect(screen.queryByRole("link", { name: /acct/i })).toBeNull();
  });
  it("on a hub extra route the index tab is highlighted; a tab that owns the path still wins", () => {
    const underlined = (name: string) => screen.getByRole("link", { name }).className.split(/\s+/).includes("border-zinc-100");
    renderAt("/h/acct-1");
    expect(underlined("Index"), "the extra belongs to the index tab's section").toBe(true);
    expect(["Keep", "Pick", "Boom"].some(underlined)).toBe(false);
    cleanup();
    renderAt("/h/keep");   // also matches the extra's ":id" pattern
    expect(underlined("Keep")).toBe(true);
    expect(underlined("Index"), "never on a path another tab owns").toBe(false);
    cleanup();
    renderAt("/h");
    expect(underlined("Index")).toBe(true);
  });
  it("on a hub extra route the index tab is current (aria-current) and no other tab is; on a tab it is not", () => {
    render(<MemoryRouter initialEntries={["/h/good"]}><Tree page={def} /></MemoryRouter>);
    expect(screen.getByRole("link", { name: "Index" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: "Keep" }).getAttribute("aria-current")).toBeNull();
    cleanup();
    render(<MemoryRouter initialEntries={["/h/keep"]}><Tree page={def} /></MemoryRouter>);
    expect(screen.getByRole("link", { name: "Index" }).getAttribute("aria-current")).toBeNull();
    expect(screen.getByRole("link", { name: "Keep" }).getAttribute("aria-current")).toBe("page");
    cleanup();
    render(<MemoryRouter initialEntries={["/h"]}><Tree page={def} /></MemoryRouter>);
    expect(screen.getByRole("link", { name: "Index" }).getAttribute("aria-current")).toBe("page");     // its own route, as before
  });
  it("the tab bar scrolls sideways instead of wrapping, and the active tab is underlined", () => {
    renderAt("/h");
    const nav = screen.getByRole("navigation", { name: "Hub sections" });
    expect(nav.className).toMatch(/\boverflow-x-auto\b/);
    for (const a of nav.querySelectorAll("a")) {
      expect(a.className).toMatch(/\bshrink-0\b/);
      expect(a.className).toMatch(/\bwhitespace-nowrap\b/);
    }
    expect(screen.getByRole("link", { name: "Index" }).className).toMatch(/\bborder-zinc-100\b/);
    expect(screen.getByRole("link", { name: "Keep" }).className).toMatch(/\bborder-transparent\b/);
  });
});
