// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

// The id of the running bundle is the <meta name="ff-build"> its index.html was served with (app/build/versionPlugin.ts); read once at load.
const load = async () => { vi.resetModules(); return (await import("./buildId")).BUILD_ID; };
afterEach(() => { document.head.querySelectorAll('meta[name="ff-build"]').forEach((m) => m.remove()); });

describe("BUILD_ID", () => {
  it("is 'dev' without the tag (vite dev, vitest, or a page cached from before it existed)", async () => {
    expect(await load()).toBe("dev");
  });
  it("is the tag's content", async () => {
    const meta = document.createElement("meta");
    meta.setAttribute("name", "ff-build"); meta.setAttribute("content", "0123456789abcdef");
    document.head.appendChild(meta);
    expect(await load()).toBe("0123456789abcdef");
  });
  it("an empty tag is 'dev', never an empty id that could never match", async () => {
    const meta = document.createElement("meta");
    meta.setAttribute("name", "ff-build"); meta.setAttribute("content", "");
    document.head.appendChild(meta);
    expect(await load()).toBe("dev");
  });
});
