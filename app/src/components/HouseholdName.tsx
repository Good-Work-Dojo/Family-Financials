// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
/** The household's own name (assumptions.json `household.name`) under the product's in the sidebar header, which on a
 *  phone is the drawer's. Plain text, shown in privacy mode too: a name is not an amount. A long name is cut with an
 *  ellipsis and carries its full text in `title`; an empty one (an export older than the key) draws nothing. */
export default function HouseholdName({ name }: { name: string }) {
  const shown = name.trim();
  if (shown === "") return null;
  return (
    <div data-testid="household-name" title={shown} className="truncate text-xs text-zinc-400">
      {shown}
    </div>
  );
}
