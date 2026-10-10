/**
 * Choosing one of a list, drawn by the app rather than by the operating system.
 *
 * The native `<select>` opened the platform's own menu — a white sheet on
 * Windows, unstyleable, with this app's colours nowhere — and that was the
 * one control on every screen that looked like a different product. This
 * keeps its contract (a label, options, `value`, `onChange` with
 * `event.target.value`) so no screen had to change, and draws the list:
 *
 * - **On a wide screen**, a menu under the field (above it when there is no
 *   room), in a portal so no card's `overflow-hidden` can clip it.
 * - **On a phone**, a sheet from the bottom edge with rows a thumb can hit,
 *   which is where every modern app puts a choice.
 * - **A long list gets a search box**: a hundred merchants is not a list
 *   anybody scrolls.
 *
 * It is the ARIA select-only combobox: the field is a `combobox` named by its
 * label, the list a `listbox` of `option`s. Arrows, Home/End, Enter, Escape
 * and typing a name all work; the motion is opacity and transform only, and
 * none of it plays for somebody who asked for reduced motion.
 */

import { Check, ChevronDown, Search, X } from "lucide-react";
import {
  type ComponentType,
  type CSSProperties,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/cn";
import { useDismissOnEscape } from "@/lib/useDismissOnEscape";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { useReducedMotion } from "@/lib/useReducedMotion";
import { useScrollLock } from "@/lib/useScrollLock";

export type Option = { value: string; label: string };

type Props = {
  label: string;
  options: Option[];
  value?: string;
  /** Shaped like a native change event, so callers read `event.target.value`. */
  onChange?: (event: { target: { value: string } }) => void;
  /** Shown as the first entry, for "no filter" — omit to force a choice. */
  placeholder?: string;
  hint?: string;
  required?: boolean;
  disabled?: boolean;
  /**
   * `field` is a form field with its label above. `pill` and `compact` are a
   * control inside a row or a toolbar: the label is still there, for a screen
   * reader, and the field says the value.
   */
  variant?: "field" | "pill" | "compact";
  icon?: ComponentType<{ className?: string }>;
  className?: string;
};

/** Past this many entries the list opens with a search box. */
const SEARCH_AFTER = 10;
/** How long typed letters are remembered when jumping by name. */
const TYPEAHEAD_MS = 600;
const MENU_MAX = 320;
const GAP = 6;

type Phase = "closed" | "open" | "leaving";
type Place = {
  left: number;
  width: number;
  maxHeight: number;
  top?: number;
  bottom?: number;
};

/** Case and accents folded away: «educacion» finds «Educación». */
function fold(text: string): string {
  return text
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase();
}

export function Select({
  label,
  options,
  value = "",
  onChange,
  placeholder,
  hint,
  required,
  disabled,
  variant = "field",
  icon: Icon,
  className,
}: Props) {
  const id = useId();
  const labelId = `${id}-label`;
  const listId = `${id}-list`;
  const hintId = `${id}-hint`;
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const typed = useRef({ text: "", at: 0 });
  /** Whether this opening has put the focus inside yet. */
  const focusedIn = useRef(false);

  const wide = useMediaQuery("(min-width: 640px)");
  const reduced = useReducedMotion();
  const [phase, setPhase] = useState<Phase>("closed");
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [place, setPlace] = useState<Place | null>(null);

  const all: Option[] = placeholder
    ? [{ value: "", label: placeholder }, ...options]
    : options;
  const searchable = options.length > SEARCH_AFTER;
  const shown = query
    ? all.filter((option) => fold(option.label).includes(fold(query)))
    : all;
  const selected = all.find((option) => option.value === value);
  const open = phase !== "closed";
  const sheet = !wide;

  const close = useCallback((refocus = true) => {
    setPhase((current) => (current === "open" ? "leaving" : current));
    if (refocus) trigger.current?.focus();
  }, []);

  // The leaving animation, then gone. Immediately, with motion reduced.
  useEffect(() => {
    if (phase !== "leaving") return;
    const timer = window.setTimeout(() => setPhase("closed"), reduced ? 0 : 180);
    return () => window.clearTimeout(timer);
  }, [phase, reduced]);

  function show() {
    if (disabled) return;
    setQuery("");
    const at = all.findIndex((option) => option.value === value);
    setActive(at === -1 ? 0 : at);
    focusedIn.current = false;
    setPhase("open");
  }

  function choose(option: Option | undefined) {
    if (!option) return;
    if (option.value !== value) onChange?.({ target: { value: option.value } });
    close();
  }

  // Where the menu goes, measured before paint so it never flashes elsewhere.
  const measure = useCallback(() => {
    const box = trigger.current?.getBoundingClientRect();
    if (!box) return;
    const width = Math.min(Math.max(box.width, 220), 360);
    const left = Math.max(8, Math.min(box.left, window.innerWidth - width - 8));
    const below = window.innerHeight - box.bottom - GAP - 8;
    const above = box.top - GAP - 8;

    setPlace(
      below >= Math.min(MENU_MAX, 200) || below >= above
        ? { left, width, top: box.bottom + GAP, maxHeight: Math.min(MENU_MAX, below) }
        : {
            left,
            width,
            bottom: window.innerHeight - box.top + GAP,
            maxHeight: Math.min(MENU_MAX, above),
          },
    );
  }, []);

  useLayoutEffect(() => {
    if (phase !== "open" || sheet) return;
    measure();
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [phase, sheet, measure]);

  // Focus into the list as it opens: the search box when there is one — but
  // not on a phone, where focusing it raises a keyboard over half the sheet.
  // The menu exists only once it has been placed, so this waits for `place`
  // rather than trying while there is nothing to focus yet.
  useEffect(() => {
    if (phase !== "open" || focusedIn.current) return;
    // On a wide screen the menu is drawn only once it has a place.
    if (!sheet && place === null) return;
    const target = searchable && !sheet ? search.current : list.current;
    if (!target) return;
    target.focus({ preventScroll: true });
    focusedIn.current = true;
  }, [phase, searchable, sheet, place]);

  // The active row stays in view as the arrows move it.
  useEffect(() => {
    if (phase !== "open") return;
    list.current
      ?.querySelector<HTMLElement>(`[data-index="${active}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [active, phase]);

  // A press anywhere else closes it, without stealing focus from what was hit.
  useEffect(() => {
    if (phase !== "open") return;
    function onDown(event: PointerEvent) {
      const target = event.target as Node;
      if (trigger.current?.contains(target) || panel.current?.contains(target)) return;
      close(false);
    }
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [phase, close]);

  useDismissOnEscape(close, phase === "open");
  useScrollLock(phase === "open" && sheet);

  function onTriggerKey(event: KeyboardEvent<HTMLButtonElement>) {
    if (["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) {
      event.preventDefault();
      show();
    }
  }

  function onListKey(event: KeyboardEvent<HTMLElement>) {
    const last = shown.length - 1;
    const inSearch = event.target === search.current;

    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((at) => Math.min(at + 1, last));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((at) => Math.max(at - 1, 0));
    } else if (event.key === "Home" && !inSearch) {
      event.preventDefault();
      setActive(0);
    } else if (event.key === "End" && !inSearch) {
      event.preventDefault();
      setActive(last);
    } else if (event.key === "Enter" || (event.key === " " && !inSearch)) {
      event.preventDefault();
      choose(shown[active]);
    } else if (event.key === "Tab") {
      event.preventDefault();
      close();
    } else if (!inSearch && event.key.length === 1 && /\S/.test(event.key)) {
      // Jump by name, like a native list: letters typed in quick succession
      // spell the start of the one wanted.
      const now = Date.now();
      const text =
        now - typed.current.at < TYPEAHEAD_MS
          ? typed.current.text + event.key
          : event.key;
      typed.current = { text, at: now };
      const wanted = fold(text);
      const order = [...shown.keys()].map((at) => (active + 1 + at) % shown.length);
      const hit = order.find((at) => fold(shown[at]?.label ?? "").startsWith(wanted));
      if (hit !== undefined) setActive(hit);
    }
  }

  const triggerLook = {
    field: cn(
      "flex w-full items-center gap-2 rounded-xl border bg-ink px-4 py-3 text-left text-base transition-colors",
      open ? "border-accent" : "border-line hover:border-muted/50",
    ),
    pill: cn(
      "inline-flex min-h-11 items-center gap-1.5 rounded-full border bg-surface px-3 text-xs transition-colors sm:min-h-9",
      open ? "border-cyan/60" : "border-line hover:border-cyan/40",
    ),
    compact: cn(
      "inline-flex min-h-11 items-center gap-1.5 rounded-lg border bg-ink px-3 text-muted text-xs transition-colors sm:min-h-9",
      open ? "border-accent" : "border-line hover:text-text",
    ),
  }[variant];

  const rows = (
    <div
      ref={list}
      id={listId}
      role="listbox"
      aria-labelledby={labelId}
      aria-activedescendant={shown[active] ? `${id}-option-${active}` : undefined}
      tabIndex={-1}
      onKeyDown={onListKey}
      className={cn(
        "overflow-y-auto overscroll-contain focus:outline-none",
        sheet ? "px-3 pb-3" : "p-1.5",
      )}
    >
      {shown.length === 0 ? (
        <p className="px-3 py-6 text-center text-faint text-sm">
          Nada coincide con «{query}».
        </p>
      ) : null}
      {shown.map((option, at) => {
        const isSelected = option.value === value;
        const isActive = at === active;
        return (
          // biome-ignore lint/a11y/useKeyWithClickEvents: the keys belong to the listbox, which keeps the focus and moves `aria-activedescendant` — the ARIA combobox pattern
          <div
            key={option.value}
            id={`${id}-option-${at}`}
            role="option"
            tabIndex={-1}
            aria-selected={isSelected}
            data-index={at}
            data-value={option.value}
            onPointerMove={() => setActive(at)}
            // Keeps the focus where it is — the search box, or the list —
            // instead of handing it to the row that was pressed.
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => choose(option)}
            className={cn(
              "flex cursor-pointer items-center gap-3 rounded-lg transition-colors duration-100",
              sheet ? "min-h-12 px-3 text-base" : "min-h-9 px-2.5 text-sm",
              isActive ? "bg-surface-raised text-text" : "text-muted",
              isSelected && "text-text",
              option.value === "" && placeholder && !isSelected && "text-faint",
            )}
          >
            <span className="min-w-0 flex-1 truncate">{option.label}</span>
            {isSelected ? (
              <Check aria-hidden className="size-4 shrink-0 text-accent" />
            ) : null}
          </div>
        );
      })}
    </div>
  );

  const searchBox = searchable ? (
    <div className={cn("relative", sheet ? "px-4 pb-2" : "p-1.5 pb-0")}>
      <Search
        aria-hidden
        className={cn(
          "-translate-y-1/2 pointer-events-none absolute top-1/2 size-4 text-faint",
          sheet ? "left-7" : "left-4",
        )}
      />
      <input
        ref={search}
        type="search"
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setActive(0);
        }}
        onKeyDown={onListKey}
        placeholder="Buscar…"
        aria-label={`Buscar en ${label}`}
        aria-controls={listId}
        aria-activedescendant={shown[active] ? `${id}-option-${active}` : undefined}
        className="w-full rounded-lg border border-line bg-ink py-2 pr-3 pl-9 text-base text-text placeholder:text-faint focus:border-accent focus:outline-none sm:text-sm"
      />
    </div>
  ) : null;

  const leaving = phase === "leaving";

  const layer = open
    ? createPortal(
        sheet ? (
          <div className="fixed inset-0 z-[70] flex flex-col justify-end">
            <div
              aria-hidden
              className={cn(
                "absolute inset-0 bg-ink/70 backdrop-blur-[2px]",
                leaving ? "fade-out" : "fade-in",
              )}
              onClick={() => close(false)}
            />
            <div
              ref={panel}
              className={cn(
                "relative flex max-h-[75dvh] flex-col rounded-t-3xl border-line border-t bg-surface pb-[env(safe-area-inset-bottom)] shadow-2xl",
                leaving ? "sheet-out" : "sheet-in",
              )}
            >
              <span
                aria-hidden
                className="mx-auto mt-2.5 h-1 w-10 rounded-full bg-line"
              />
              <div className="flex items-center justify-between gap-3 px-5 pt-2 pb-3">
                <p className="font-medium text-sm">{label}</p>
                <button
                  type="button"
                  onClick={() => close()}
                  aria-label="Cerrar"
                  className="-mr-2 grid size-11 place-items-center rounded-xl text-faint hover:text-text"
                >
                  <X className="size-4" aria-hidden />
                </button>
              </div>
              {searchBox}
              {rows}
            </div>
          </div>
        ) : place ? (
          <div
            ref={panel}
            style={
              {
                left: place.left,
                width: place.width,
                top: place.top,
                bottom: place.bottom,
                maxHeight: place.maxHeight,
                "--menu-from": place.top === undefined ? "6px" : "-6px",
              } as CSSProperties
            }
            className={cn(
              "fixed z-[70] flex flex-col overflow-hidden rounded-xl border border-line bg-surface shadow-2xl shadow-black/40",
              place.top === undefined ? "origin-bottom" : "origin-top",
              leaving ? "menu-out" : "menu-in",
            )}
          >
            {searchBox}
            {rows}
          </div>
        ) : null,
        document.body,
      )
    : null;

  return (
    <div
      className={cn(
        "relative min-w-0",
        variant === "field" ? "flex flex-col gap-2" : "inline-flex",
      )}
    >
      <span
        id={labelId}
        className={variant === "field" ? "text-muted text-sm" : "sr-only"}
      >
        {label}
      </span>
      <button
        ref={trigger}
        type="button"
        role="combobox"
        aria-labelledby={labelId}
        aria-controls={listId}
        aria-expanded={phase === "open"}
        aria-haspopup="listbox"
        aria-describedby={hint ? hintId : undefined}
        disabled={disabled}
        onClick={() => (phase === "open" ? close(false) : show())}
        onKeyDown={onTriggerKey}
        className={cn(
          triggerLook,
          "focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
          className,
        )}
      >
        {Icon ? <Icon aria-hidden className="size-3.5 shrink-0 text-faint" /> : null}
        <span
          className={cn(
            "min-w-0 flex-1 truncate",
            !selected || (selected.value === "" && placeholder) ? "text-faint" : "",
            variant === "field" && !selected && "text-faint",
          )}
        >
          {selected?.label ?? placeholder ?? "Elige una opción"}
        </span>
        <ChevronDown
          aria-hidden
          className={cn(
            "size-4 shrink-0 text-faint transition-transform duration-200",
            open && phase === "open" && "rotate-180",
            variant !== "field" && "size-3.5",
          )}
        />
      </button>

      {/* A form's `required` still stops it from submitting, and the browser's
          message lands on the field: the input is under it, invisible. */}
      {required ? (
        <input
          tabIndex={-1}
          aria-hidden
          required
          value={value}
          onChange={() => undefined}
          onInvalid={() => trigger.current?.focus()}
          className="pointer-events-none absolute bottom-0 left-4 h-px w-px opacity-0"
        />
      ) : null}

      {hint ? (
        <p id={hintId} className="text-faint text-xs">
          {hint}
        </p>
      ) : null}
      {layer}
    </div>
  );
}
