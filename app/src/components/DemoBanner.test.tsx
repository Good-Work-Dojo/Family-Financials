// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { IS_DEMO } from "../lib/demo";
import DemoBanner from "./DemoBanner";

afterEach(cleanup);

// The demo build's own behaviour is src/demoMode.ui.test.tsx's; this is every other build.
describe("the demo banner outside the demo build", () => {
  it("draws nothing", () => {
    expect(IS_DEMO).toBe(false);
    const { container } = render(<DemoBanner />);
    expect(container.innerHTML).toBe("");
  });
});
