// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useCoarsePointer, useIsMobile } from "./useMediaQuery";

/** The words a page uses for acting on what it shows, chosen by the POINTER, never by the screen's width: a finger
 *  (`(pointer: coarse)`) taps and has no keyboard shortcut to be told about, a mouse clicks and has ⌘K. A narrow window
 *  on a desktop still clicks; a tablet wider than the phone cut still taps. One word also needs the width: the way into
 *  the jump-anywhere palette is the top bar's 🔍 button only where there is one - a finger below the phone cut. */
export interface PointerWords {
  /** the main pointer is a finger */
  touch: boolean;
  /** "tap" / "click", and capitalised */
  click: "tap" | "click";
  Click: "Tap" | "Click";
  /** the palette is reached by the top bar's 🔍 button: a finger on a phone (the bar is there below the cut only) */
  searchButton: boolean;
  /** the way into the jump-anywhere palette in a sentence: "search" (the 🔍 button) where `searchButton`, else "⌘K" */
  search: "search" | "⌘K";
  /** what a chart's zoom asks of its pointer: a pinch, or the scroll wheel */
  zoom: string;
}

/** `touch`: the main pointer is a finger; `phone`: the screen is below the phone cut (where the top bar and its 🔍 are) */
export function pointerWords(touch: boolean, phone: boolean): PointerWords {
  const searchButton = touch && phone;
  const search = searchButton ? "search" : "⌘K";
  return touch
    ? { touch, click: "tap", Click: "Tap", searchButton, search, zoom: "Pinch the chart to zoom." }
    : { touch, click: "click", Click: "Click", searchButton, search, zoom: "Scroll on the chart to zoom." };
}

/** pointerWords for this device's main pointer (useCoarsePointer) and width (useIsMobile), following both. */
export function usePointerWords(): PointerWords {
  return pointerWords(useCoarsePointer(), useIsMobile());
}
