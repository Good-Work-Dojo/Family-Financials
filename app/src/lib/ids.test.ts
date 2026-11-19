// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { PREVIEW_ID, PREVIEW_STAMP, uuid4 } from "./ids";

describe("ids", () => {
  it("the save-preview placeholder is exactly as long as a real id, so a preview and the real save agree at the byte boundary", () => {
    expect(PREVIEW_ID).toHaveLength(uuid4().length);
    expect(PREVIEW_ID).toHaveLength(36);
  });
  it("the save-preview stamp is a constant exactly as long as a real created_at (toISOString), so the preview reads no clock", () => {
    expect(PREVIEW_STAMP).toHaveLength(new Date(0).toISOString().length);
    expect(PREVIEW_STAMP).toHaveLength(new Date(Date.UTC(2099, 11, 31, 23, 59, 59, 999)).toISOString().length);
    expect(PREVIEW_STAMP).toHaveLength(24);
  });
});
