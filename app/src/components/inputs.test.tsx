// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MASK, setPrivate } from "../lib/privacy";
import { AmountRange, CurrencyInput, MaskedNumberInput, NumberInput, SliderRow, TagInput } from "./inputs";

afterEach(() => { cleanup(); setPrivate(false); });

describe("inputs under privacy mode", () => {
  it("CurrencyInput shows the mask at rest and the real number while focused; commits still work", () => {
    setPrivate(true);
    const onCommit = vi.fn();
    render(<CurrencyInput value={650000} onCommit={onCommit} />);
    const box = screen.getByRole("textbox") as HTMLInputElement;
    expect(box.value).toBe(MASK);
    fireEvent.focus(box);
    expect(box.value).toBe("650000");
    fireEvent.change(box, { target: { value: "700k" } });
    fireEvent.blur(box);
    expect(onCommit).toHaveBeenCalledWith(700000);
    expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe(MASK);
  });
  it("CurrencyInput at rest: whole dollars when the amount is whole to the cent, else to the cent", () => {
    const shown = (v: number) => {
      const { unmount } = render(<CurrencyInput value={v} onCommit={() => {}} />);
      const text = (screen.getByRole("textbox") as HTMLInputElement).value;
      unmount();
      return text;
    };
    expect([shown(1400), shown(2746.4), shown(50_000.004), shown(33.5)]).toEqual(["1,400", "2,746.40", "50,000", "33.50"]);
  });
  it("CurrencyInput's empty-box placeholder is the readable fine-print token, not the browser's default tint", () => {
    render(<CurrencyInput value={0} onCommit={() => {}} />);
    const box = screen.getByRole("textbox") as HTMLInputElement;
    expect(box.placeholder).toBe("0");
    expect(box.className).toContain("placeholder:text-faint");
  });
  it("NumberInput masks a $ field at rest and never a % field", () => {
    setPrivate(true);
    render(<><NumberInput value={1500} onCommit={() => {}} min={0} max={5000} step={100} unit="$" />
             <NumberInput value={7} onCommit={() => {}} min={0} max={12} step={0.5} unit="%" /></>);
    expect((screen.getByDisplayValue(MASK) as HTMLInputElement).readOnly).toBe(true);
    expect(screen.getByDisplayValue("7")).toBeDefined();
    fireEvent.focus(screen.getByDisplayValue(MASK));
    expect(screen.getByDisplayValue("1500")).toBeDefined();
  });
  it("a $ NumberInput is editable while focused (the commit goes through) and masks again on blur", () => {
    setPrivate(true);
    const onCommit = vi.fn();
    render(<NumberInput value={1500} onCommit={onCommit} min={0} max={5000} step={100} unit="$" />);
    fireEvent.focus(screen.getByDisplayValue(MASK));
    const real = screen.getByDisplayValue("1500") as HTMLInputElement;
    expect(real.readOnly).toBe(false);
    fireEvent.change(real, { target: { value: "2000" } });
    expect(onCommit).toHaveBeenCalledWith(2000);
    expect(screen.queryByDisplayValue(MASK), "still being edited: never re-masked mid-keystroke").toBeNull();
    fireEvent.blur(screen.getByDisplayValue("1500"));
    expect((screen.getByDisplayValue(MASK) as HTMLInputElement).readOnly).toBe(true);
  });
  it("with privacy off nothing is masked", () => {
    render(<NumberInput value={1500} onCommit={() => {}} min={0} max={5000} step={100} unit="$" />);
    expect(screen.getByDisplayValue("1500")).toBeDefined();
  });
  it("turning privacy on or off re-renders a mounted input at once", () => {
    render(<NumberInput value={1500} onCommit={() => {}} min={0} max={5000} step={100} unit="$" />);
    expect(screen.queryByDisplayValue(MASK)).toBeNull();
    act(() => setPrivate(true));
    expect(screen.getByDisplayValue(MASK)).toBeDefined();
    act(() => setPrivate(false));
    expect(screen.getByDisplayValue("1500")).toBeDefined();
  });
});

describe("CurrencyInput: an amount nobody has set", () => {
  const typeInto = (box: HTMLInputElement, text: string) => {
    fireEvent.focus(box); fireEvent.change(box, { target: { value: text } }); fireEvent.blur(box);
  };
  it("null is an empty box, with no placeholder and no mask, and any number typed into it is committed, 0 included", () => {
    setPrivate(true);
    const onCommit = vi.fn();
    render(<CurrencyInput value={null} onCommit={onCommit} />);
    const box = screen.getByRole("textbox") as HTMLInputElement;
    expect(box.value, "nothing to hide in an empty box").toBe("");
    expect(box.getAttribute("placeholder")).toBeNull();
    typeInto(box, "0");
    expect(onCommit).toHaveBeenCalledWith(0);
  });
  it("a number behaves as before: the same amount typed commits nothing; an emptied box keeps its amount without onClear", () => {
    const onCommit = vi.fn();
    render(<CurrencyInput value={0} onCommit={onCommit} />);
    const box = screen.getByRole("textbox") as HTMLInputElement;
    expect(box.getAttribute("placeholder")).toBe("0");
    typeInto(box, "0");
    typeInto(box, "");
    expect(onCommit).not.toHaveBeenCalled();
    typeInto(box, "250");
    expect(onCommit).toHaveBeenCalledWith(250);
  });
  it("with onClear, emptying a box that held an amount calls it; focusing and leaving a box of 0 does not", () => {
    const onClear = vi.fn();
    render(<CurrencyInput value={0} onCommit={() => {}} onClear={onClear} />);
    const box = screen.getByRole("textbox") as HTMLInputElement;
    fireEvent.focus(box);
    expect(box.value, "the edit starts at 0, selected").toBe("0");
    fireEvent.blur(box);
    expect(onClear).not.toHaveBeenCalled();
    typeInto(box, "");
    expect(onClear).toHaveBeenCalledTimes(1);
  });
});

describe("SliderRow under privacy mode", () => {
  const row = (unit: string) => render(<SliderRow label="Amount" value={1500} onCommit={() => {}} min={0} max={5000} step={100} unit={unit} />);
  it("blurs a $ slider (its thumb is the number) and says its value is hidden to a screen reader; a % slider is untouched", () => {
    setPrivate(true);
    const { unmount } = row("$");
    const slider = screen.getByRole("slider");
    expect(slider.className).toMatch(/\bblur-sm\b/);
    expect(slider.getAttribute("aria-valuetext")).toBe("amount hidden");
    expect(screen.getByDisplayValue(MASK)).toBeDefined();
    unmount();
    row("%");
    expect(screen.getByRole("slider").className).not.toMatch(/blur/);
    expect(screen.getByRole("slider").hasAttribute("aria-valuetext")).toBe(false);
  });
  it("with privacy off a $ slider is plain", () => {
    row("$");
    expect(screen.getByRole("slider").className).not.toMatch(/blur/);
    expect(screen.getByRole("slider").hasAttribute("aria-valuetext")).toBe(false);
  });
});

describe("AmountRange: a slider over an amount", () => {
  const attrs = (el: HTMLElement) => ["min", "max", "step", "value"].map((a) => el.getAttribute(a));
  it("in privacy mode holds no amount (it runs over its steps, the thumb where the amount puts it) and a move commits the amount at that step", () => {
    setPrivate(true);
    const onCommit = vi.fn();
    render(<AmountRange value={1234.56} min={500} max={6000} step={50} onCommit={onCommit} className="w-full" />);
    const slider = screen.getByRole("slider") as HTMLInputElement;
    expect(attrs(slider), "steps 0 to 110, the thumb at the 15th (1,250, where the plain slider puts 1,234.56)").toEqual(["0", "110", "1", "15"]);
    expect(slider.className).toBe("w-full blur-sm");
    expect(slider.getAttribute("aria-valuetext")).toBe("amount hidden");
    fireEvent.change(slider, { target: { value: "45" } });
    expect(onCommit).toHaveBeenLastCalledWith(2750);
    fireEvent.change(slider, { target: { value: "110" } });
    expect(onCommit).toHaveBeenLastCalledWith(6000);
  });
  it("a range that is no whole number of steps ends at its last whole step, and an amount outside it sits at an end", () => {
    setPrivate(true);
    const { rerender } = render(<AmountRange value={-5} min={0} max={1050} step={100} onCommit={() => {}} className="w-full" />);
    expect(attrs(screen.getByRole("slider"))).toEqual(["0", "10", "1", "0"]);
    rerender(<AmountRange value={99_999} min={0} max={1050} step={100} onCommit={() => {}} className="w-full" />);
    expect(attrs(screen.getByRole("slider"))).toEqual(["0", "10", "1", "10"]);
  });
  it("with privacy off is the plain slider over the amounts", () => {
    const onCommit = vi.fn();
    render(<AmountRange value={1234.56} min={500} max={6000} step={50} onCommit={onCommit} className="w-full" />);
    const slider = screen.getByRole("slider");
    expect(attrs(slider)).toEqual(["500", "6000", "50", "1234.56"]);
    expect(slider.className).toBe("w-full");
    expect(slider.hasAttribute("aria-valuetext")).toBe(false);
    fireEvent.change(slider, { target: { value: "2750" } });
    expect(onCommit).toHaveBeenLastCalledWith(2750);
  });
});

describe("MaskedNumberInput", () => {
  it("masks a filled box at rest (keeping its class and name) and reveals it on focus", () => {
    setPrivate(true);
    render(<MaskedNumberInput type="number" value={1200} aria-label="Budget" className="w-24" onChange={() => {}} />);
    const box = screen.getByLabelText("Budget") as HTMLInputElement;
    expect(box.value).toBe(MASK);
    expect(box.readOnly).toBe(true);
    expect(box.className).toBe("w-24");
    fireEvent.focus(box);
    expect((screen.getByLabelText("Budget") as HTMLInputElement).value).toBe("1200");
  });
  it("hands the focus to the real input when it swaps in (autoFocus only works on mount), and takes it back to the mask on blur", () => {
    setPrivate(true);
    render(<MaskedNumberInput type="number" value={1200} aria-label="Budget" onChange={() => {}} />);
    const masked = screen.getByLabelText("Budget") as HTMLInputElement;
    expect(document.activeElement).not.toBe(masked);
    fireEvent.focus(masked);                                              // a synthetic focus event: nothing is really focused yet
    const real = screen.getByLabelText("Budget") as HTMLInputElement;
    expect(real.type).toBe("number");
    expect(document.activeElement, "the real input holds the focus").toBe(real);
    fireEvent.blur(real);
    expect((screen.getByLabelText("Budget") as HTMLInputElement).value).toBe(MASK);
  });
  it("two masked boxes with word placeholders stay distinct: the placeholder names each one (an aria-label wins) and leads its title", () => {
    setPrivate(true);
    render(<><MaskedNumberInput type="number" value={500} placeholder="min $" onChange={() => {}} />
             <MaskedNumberInput type="number" value={900} placeholder="max $" aria-label="Maximum amount" onChange={() => {}} /></>);
    const [min, max] = screen.getAllByDisplayValue(MASK) as HTMLInputElement[];
    expect(min.getAttribute("aria-label")).toBe("min $");
    expect(min.title).toBe("min $ \u2014 hidden by privacy mode \u2014 focus to edit");
    expect(max.getAttribute("aria-label")).toBe("Maximum amount");
    expect(screen.getByLabelText("min $")).toBe(min);
  });
  it("an EMPTY box has no amount to hide, but a numeric placeholder (an automatic baseline) is masked until it is focused", () => {
    setPrivate(true);
    render(<MaskedNumberInput type="number" value="" placeholder="1234" aria-label="Budget" onChange={() => {}} />);
    const box = () => screen.getByLabelText("Budget") as HTMLInputElement;
    expect(box().value).toBe("");
    expect(box().readOnly).toBe(false);
    expect(box().placeholder).toBe(MASK);
    fireEvent.focus(box());
    expect(box().placeholder).toBe("1234");
    fireEvent.blur(box());
    expect(box().placeholder).toBe(MASK);
  });
  it("a placeholder with no digits (a dash) is left alone, and nothing is touched with privacy off", () => {
    setPrivate(true);
    const { unmount } = render(<MaskedNumberInput type="number" value="" placeholder="—" aria-label="Budget" onChange={() => {}} />);
    expect((screen.getByLabelText("Budget") as HTMLInputElement).placeholder).toBe("—");
    unmount();
    setPrivate(false);
    render(<MaskedNumberInput type="number" value={5} placeholder="1234" aria-label="Budget" onChange={() => {}} />);
    expect((screen.getByLabelText("Budget") as HTMLInputElement).value).toBe("5");
    expect((screen.getByLabelText("Budget") as HTMLInputElement).placeholder).toBe("1234");
  });
});

describe("TagInput", () => {
  it("is a 40px tap target below md and compact from md, with 16px text below sm (no iOS focus-zoom)", () => {
    render(<TagInput id="t" value={["home"]} onChange={() => {}} suggestions={[]} />);
    const cls = screen.getByPlaceholderText("add tag…").className.split(/\s+/);
    for (const c of ["min-h-[40px]", "md:min-h-0", "text-base", "sm:text-xs"]) expect(cls, c).toContain(c);
  });
});
