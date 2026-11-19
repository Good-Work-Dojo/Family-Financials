// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { scrollHint } from "./useScrollHint";

describe("scrollHint", () => {
  it("content that fits is no overflow and no more, a pixel of rounding included", () => {
    expect(scrollHint({ scrollLeft: 0, clientWidth: 358, scrollWidth: 358 })).toEqual({ overflows: false, more: false });
    expect(scrollHint({ scrollLeft: 0, clientWidth: 358, scrollWidth: 359 })).toEqual({ overflows: false, more: false });
  });

  it("wider content has more to the right until the scroller reaches its end", () => {
    expect(scrollHint({ scrollLeft: 0, clientWidth: 358, scrollWidth: 520 })).toEqual({ overflows: true, more: true });
    expect(scrollHint({ scrollLeft: 100, clientWidth: 358, scrollWidth: 520 })).toEqual({ overflows: true, more: true });
    expect(scrollHint({ scrollLeft: 162, clientWidth: 358, scrollWidth: 520 })).toEqual({ overflows: true, more: false });
    // a fractional scroll position a pixel short of the end is the end
    expect(scrollHint({ scrollLeft: 161.5, clientWidth: 358, scrollWidth: 520 })).toEqual({ overflows: true, more: false });
  });
});
