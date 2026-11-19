// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { snapFactory } from "../lib/__fixtures__";
import type { Snapshot } from "./types";

// the loaded snapshots (data.assets.snapshots); a test swaps the list to say "a reload brought new exports"
const world = vi.hoisted(() => ({ snapshots: [] as Snapshot[] }));
vi.mock("./DataContext", () => ({ useData: () => ({ data: { assets: { snapshots: world.snapshots } } }) }));
import { snapshotsOf, useSnapshots } from "./useSnapshots";

const snap = snapFactory();
const MAY = snap({ snapshot_date: "2026-05-31" });
const JUNE = snap({ snapshot_date: "2026-06-30" });
const NOW = snap({ snapshot_date: "2026-07-14" });

describe("snapshotsOf: the newest snapshot and the one before it, or null", () => {
  it("no snapshot: nothing is the latest and nothing the prior", () => {
    const list: Snapshot[] = [];
    expect(snapshotsOf(list)).toEqual({ all: [], latest: null, prior: null });
    expect(snapshotsOf(list).all, "the list itself, not a copy").toBe(list);
  });
  it("one snapshot is the latest, with no prior: never a comparison with itself", () => {
    const s = snapshotsOf([NOW]);
    expect(s.latest).toBe(NOW);
    expect(s.prior).toBeNull();
  });
  it("more: the last element is the latest and the one before it the prior", () => {
    const list = [MAY, JUNE, NOW];
    const s = snapshotsOf(list);
    expect(s.all).toBe(list);
    expect(s.latest).toBe(NOW);
    expect(s.prior).toBe(JUNE);
    expect(snapshotsOf([JUNE, NOW]).prior).toBe(JUNE);
  });
});

describe("useSnapshots reads the loaded snapshots", () => {
  it("is snapshotsOf(data.assets.snapshots)", () => {
    world.snapshots = [MAY, NOW];
    const { result } = renderHook(() => useSnapshots());
    expect(result.current).toEqual({ all: world.snapshots, latest: NOW, prior: MAY });
    expect(result.current.all).toBe(world.snapshots);
  });
  it("with no snapshot loaded both are null", () => {
    world.snapshots = [];
    expect(renderHook(() => useSnapshots()).result.current).toEqual({ all: [], latest: null, prior: null });
  });
  it("is one object while the list is the same one, and a new one when the list changes", () => {
    world.snapshots = [MAY, NOW];
    const { result, rerender } = renderHook(() => useSnapshots());
    const first = result.current;
    rerender();
    expect(result.current, "the same list: the same object").toBe(first);
    world.snapshots = [MAY, JUNE, NOW];
    rerender();
    expect(result.current).not.toBe(first);
    expect(result.current.prior).toBe(JUNE);
  });
});
