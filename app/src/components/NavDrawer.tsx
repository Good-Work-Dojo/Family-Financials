// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { type ReactNode, type RefObject, useRef } from "react";
import { useFocusTrap } from "../lib/useFocusTrap";
import { useIsMobile } from "../lib/useMediaQuery";

/** the drawer's id: the top bar's menu button names it in `aria-controls` */
export const NAV_DRAWER_ID = "nav-drawer";

/** The sidebar: a sticky rail on a desktop; below `md` an off-canvas drawer the top bar's ☰ opens. Moved off the
 *  canvas is not closed for a keyboard or a screen reader, so the closed drawer is inert (no tab stop, nothing read
 *  between the menu button and the page). Open, it is a modal dialog: focus moves to its first control, Tab stays
 *  inside, Escape closes it and focus goes back to what opened it (lib/useFocusTrap.ts). */
export default function NavDrawer({ open, onClose, className, children }: {
  open: boolean;
  onClose: () => void;
  className?: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLElement>(null);
  const phone = useIsMobile();
  // React 18 passes `inert` through to the DOM as an unknown attribute: present (an empty string) or left out
  const state = !phone ? {} : open ? { role: "dialog", "aria-modal": true, "aria-label": "Menu" } : { inert: "" };
  return (
    <aside ref={ref} id={NAV_DRAWER_ID} className={className} {...state}>
      {phone && open && <DrawerTrap target={ref} onClose={onClose} />}
      {children}
    </aside>
  );
}

/** mounted only while the phone's drawer is open: the focus trap reads its element once, on mount */
function DrawerTrap({ target, onClose }: { target: RefObject<HTMLElement | null>; onClose: () => void }) {
  useFocusTrap(target, onClose);
  return null;
}
