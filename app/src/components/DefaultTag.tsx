// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { cardPath, SCROLL_TARGETS } from "../lib/scrollTargets";

const TAG = "ml-1 inline-block whitespace-nowrap rounded border border-zinc-600 px-1 align-middle text-[10px] font-normal "
  + "normal-case leading-4 tracking-normal text-zinc-400";

/** The small "default" tag beside a figure, or beside the control that holds the value, when it rests on a value nobody
 *  in the household stated: a projection default the configuration and the app leave unset (lib/projectionDefaults.ts),
 *  or a control's built-in starting value the person has not changed. It is a word, not a colour, and its title says
 *  which default it is and where to change it (a screen reader hears the title too). `toData`: the default is one Data's
 *  Projection defaults card states, so the tag is a link that lands on that card (`?card=`); otherwise the control
 *  beside it is where it changes. `focusable={false}` inside something interactive (no nested tab stop). */
export default function DefaultTag({ title, toData = false, focusable = true }: {
  title: string; toData?: boolean; focusable?: boolean;
}) {
  const words = <>default<span className="sr-only">: {title}</span></>;
  if (toData) {
    return (
      <a href={`#${cardPath("/data", SCROLL_TARGETS.projectionDefaults)}`} title={title} data-testid="default-tag"
        tabIndex={focusable ? undefined : -1} className={`${TAG} hover:text-zinc-200`}>{words}</a>
    );
  }
  return (
    <span title={title} data-testid="default-tag" tabIndex={focusable ? 0 : undefined} className={TAG}>{words}</span>
  );
}
