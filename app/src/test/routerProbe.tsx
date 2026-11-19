// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// The one router-location probe for component tests: mount <Where /> inside the test's MemoryRouter next to the
// component, then read where the component navigated. A <span>, not an <output>: an output is a status role and
// would be one more `status` for a test that queries those.
import { screen } from "@testing-library/react";
import { useLocation } from "react-router-dom";

export function Where() {
  const l = useLocation();
  return <span data-testid="where" data-search={l.search}>{l.pathname}{l.search}|{JSON.stringify(l.state)}</span>;
}

/** `pathname` + `search` + "|" + the JSON of the navigation state, e.g. `/spending/transactions?x=1|{"carriedSearch":true}` */
export const where = (): string => screen.getByTestId("where").textContent ?? "";

/** the location's query alone, parsed */
export const whereSearch = (): URLSearchParams => new URLSearchParams(screen.getByTestId("where").dataset.search ?? "");
