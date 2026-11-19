// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useMemo } from "react";
import { useData } from "../data/DataContext";
import { splitNote, splitRules } from "../lib/splits";

/** The one-line footnote under a category view when a category it shows contains split parts (a vendor rule's
 *  allocation, e.g. 80% of each bill in Electric and 20% in Water): the Budget grid, the income statement,
 *  Explore and the emergency-fund card all moved when the rule landed, and only the register said why. Shares come from
 *  the parts themselves (lib/splits); the Data page lists every rule. Renders nothing when no shown category holds a part. */
export default function SplitNote({ categories }: { categories: readonly string[] }) {
  const { data, indexes } = useData();
  const rules = useMemo(() => splitRules(data.splitParents, indexes.childrenByParent), [data.splitParents, indexes.childrenByParent]);
  const note = useMemo(() => splitNote(rules, categories), [rules, categories]);
  if (!note) return null;
  return (
    <p data-testid="split-note" className="mt-1 text-[10px] text-faint">
      Split rule: {note}. <a href="#/data" className="text-blue-400 hover:underline">Data → Split rules</a>
    </p>
  );
}
