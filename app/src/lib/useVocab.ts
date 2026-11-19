// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useData } from "../data/DataContext";
import { type CategoryVocab, categoryVocab } from "./categories";

/** The category vocabulary of the loaded data - its reserved names read through its renames (lib/categories): what a
 *  page asks "is this essential / the uncategorized pile / Internal" with. One object per data set, so it is a stable
 *  dependency. */
export function useVocab(): CategoryVocab {
  return categoryVocab(useData().data.assumptions);
}
