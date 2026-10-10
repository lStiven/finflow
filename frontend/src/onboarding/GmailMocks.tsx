/**
 * Pictures of the few corners of Gmail the setup happens in.
 *
 * Schematic on purpose: only what the step is about carries Gmail's own
 * words — the ones the guide has been checked against — and everything else
 * is a grey bar. A drawing that named every option would be naming options
 * from memory, and the day Gmail renames one the picture would contradict
 * the screen it is meant to explain. None of this is Gmail's artwork either;
 * it is drawn in Finflow's own colours.
 *
 * All of it is decorative to a screen reader: each slide says the same thing
 * in words.
 */

import {
  ArrowDown,
  Check,
  ChevronDown,
  Search,
  SlidersHorizontal,
  X,
} from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/** A window with Gmail in it, so every picture reads as "this is Gmail". */
export function GmailFrame({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      aria-hidden
      className={cn(
        "w-full select-none overflow-hidden rounded-xl border border-line bg-surface text-[0.6875rem] shadow-[0_12px_32px_-18px] shadow-black/70",
        className,
      )}
    >
      <div className="flex items-center gap-1.5 border-line border-b bg-ink/70 px-3 py-2">
        <span className="size-2 rounded-full bg-line" />
        <span className="size-2 rounded-full bg-line" />
        <span className="size-2 rounded-full bg-line" />
        <span className="ml-2 rounded-md bg-surface-raised px-2 py-0.5 text-[0.625rem] text-faint">
          Gmail
        </span>
      </div>
      <div className="p-3 sm:p-4">{children}</div>
    </div>
  );
}

/** A label that is not the point of this picture. */
function Bar({ width, className }: { width: string; className?: string }) {
  return (
    <span
      className={cn("block h-2 shrink-0 rounded-full bg-line", className)}
      style={{ width }}
    />
  );
}

/** What to press: outlined, with a halo while motion is allowed. */
function Spot({
  on,
  tip,
  tipAt = "center",
  children,
  className,
}: {
  on: boolean;
  /** Gmail's own name for it, shown the way Gmail shows a tooltip. */
  tip?: string;
  /** Under the middle, or flush with the right edge for a control there. */
  tipAt?: "center" | "end";
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn("relative inline-flex rounded-md", on && "spotlight", className)}
    >
      {children}
      {on && tip ? (
        <span
          className={cn(
            "absolute top-full z-10 mt-2.5 whitespace-nowrap rounded-md bg-text px-2 py-1 font-medium text-[0.625rem] text-ink shadow-lg",
            tipAt === "end" ? "right-0" : "-translate-x-1/2 left-1/2",
          )}
        >
          {tip}
        </span>
      ) : null}
    </span>
  );
}

function InboxRows() {
  return (
    <div className="mt-3 flex flex-col gap-2.5 px-1">
      {["62%", "48%", "70%"].map((width) => (
        <div key={width} className="flex items-center gap-3">
          <Bar width="18%" className="bg-surface-raised" />
          <Bar width={width} className="bg-surface-raised" />
        </div>
      ))}
    </div>
  );
}

/** The search box, and the button at its right that opens the options. */
export function SearchBarMock() {
  return (
    <GmailFrame>
      <div className="flex items-center gap-2 rounded-full bg-surface-raised px-3 py-2">
        <Search className="size-3.5 shrink-0 text-faint" />
        <Bar width="34%" />
        <span className="flex-1" />
        <Spot on tip="Mostrar opciones de búsqueda" tipAt="end">
          <span className="grid size-6 place-items-center rounded-md text-text">
            <SlidersHorizontal className="size-3.5" />
          </span>
        </Spot>
      </div>
      <div className="h-8" />
      <InboxRows />
    </GmailFrame>
  );
}

/**
 * The search options panel. «De» is the only field named: it is the only one
 * the filter uses, and the rest must stay empty.
 */
export function SearchOptionsMock({
  from,
  highlight,
}: {
  /** What is in «De» — the person's own filter, as they will paste it. */
  from: string;
  highlight: "from" | "create";
}) {
  return (
    <GmailFrame>
      <div className="flex flex-col gap-2.5 rounded-lg border border-line bg-surface-raised p-3">
        <div className="flex items-center gap-3">
          <span className="w-7 shrink-0 text-muted">De</span>
          <Spot on={highlight === "from"} className="min-w-0 flex-1">
            <span className="block w-full truncate rounded border border-line bg-ink px-2 py-1 font-mono text-[0.625rem] text-text">
              {from}
            </span>
          </Spot>
        </div>
        {["12%", "15%", "22%"].map((label) => (
          <div key={label} className="flex items-center gap-3">
            <Bar width={label} />
            <span className="h-5 flex-1 rounded border border-line/70 bg-ink/50" />
          </div>
        ))}
        <div className="mt-1 flex items-center justify-end gap-4">
          <Spot on={highlight === "create"}>
            <span className="px-1 py-0.5 text-text">Crear filtro</span>
          </Spot>
          <span className="rounded-md bg-line px-3 py-1 text-muted">Buscar</span>
        </div>
      </div>
    </GmailFrame>
  );
}

/** What «Buscar» should turn up: mail from the banks just chosen. */
export function ResultsMock({ banks }: { banks: string[] }) {
  const rows = (banks.length > 0 ? banks : ["Tu banco"]).slice(0, 3);

  return (
    <GmailFrame>
      {/* The bottom of the options panel, where «Buscar» is, and what it
          turns up underneath. */}
      <div className="flex items-center justify-end gap-4 rounded-lg border border-line bg-surface-raised px-3 py-2">
        <span className="text-muted">Crear filtro</span>
        <Spot on>
          <span className="rounded-md bg-accent px-3 py-1 font-semibold text-accent-ink">
            Buscar
          </span>
        </Spot>
      </div>
      <div className="my-1.5 flex justify-center">
        <ArrowDown className="size-3.5 text-faint" />
      </div>
      <ul className="flex flex-col">
        {rows.map((bank, index) => (
          <li
            key={bank}
            className={cn(
              "flex items-center gap-3 border-line/60 px-1 py-2",
              index > 0 && "border-t",
            )}
          >
            <span className="w-24 shrink-0 truncate font-semibold text-text">
              {bank}
            </span>
            <Bar width="45%" className="bg-surface-raised" />
            <Check
              className="ml-auto size-3.5 shrink-0 text-incoming"
              strokeWidth={3}
            />
          </li>
        ))}
      </ul>
    </GmailFrame>
  );
}

/**
 * The filter's actions. «Reenviarlo a» is the only one named, with the
 * person's own address in it; the rest are left as they are.
 */
export function FilterActionsMock({
  address,
  highlight,
}: {
  address: string;
  highlight: "forward" | "create";
}) {
  return (
    <GmailFrame>
      <div className="flex flex-col gap-2.5 rounded-lg border border-line bg-surface-raised p-3">
        {["38%", "26%"].map((width) => (
          <div key={width} className="flex items-center gap-2">
            <span className="size-3 shrink-0 rounded-sm border border-line" />
            <Bar width={width} />
          </div>
        ))}
        <Spot on={highlight === "forward"} className="w-full">
          <span className="flex w-full min-w-0 items-center gap-2 py-0.5">
            <span className="grid size-3 shrink-0 place-items-center rounded-sm bg-accent text-accent-ink">
              <Check className="size-2.5" strokeWidth={3.5} />
            </span>
            <span className="shrink-0 text-text">Reenviarlo a:</span>
            <span className="flex min-w-0 items-center gap-1 rounded border border-line bg-ink px-1.5 py-0.5">
              <span className="truncate font-mono text-[0.625rem]">{address}</span>
              <ChevronDown className="size-3 shrink-0 text-faint" />
            </span>
          </span>
        </Spot>
        {["30%", "22%"].map((width) => (
          <div key={width} className="flex items-center gap-2">
            <span className="size-3 shrink-0 rounded-sm border border-line" />
            <Bar width={width} />
          </div>
        ))}
        <div className="mt-1 flex justify-end">
          <Spot on={highlight === "create"}>
            <span className="rounded-md bg-accent px-3 py-1 font-semibold text-accent-ink">
              Crear filtro
            </span>
          </Spot>
        </div>
      </div>
    </GmailFrame>
  );
}

/** Settings, with the forwarding tab and the button that adds an address. */
export function ForwardingTabMock({ highlight }: { highlight: "tab" | "add" }) {
  return (
    <GmailFrame>
      <p className="mb-2 font-medium text-[0.75rem] text-text">Configuración</p>
      {/* Not clipped: the halo around the tab has to show past the row. */}
      <div className="flex items-center gap-3 border-line border-b pt-1 pb-2.5">
        <Bar width="10%" className="shrink" />
        <Bar width="9%" className="shrink max-sm:hidden" />
        <Spot on={highlight === "tab"} className="shrink-0">
          <span
            className={cn(
              "whitespace-nowrap px-1 py-0.5",
              highlight === "tab" ? "text-text" : "text-muted",
            )}
          >
            Reenvío y correo POP/IMAP
          </span>
        </Spot>
        <Bar width="9%" className="shrink max-sm:hidden" />
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="text-muted">Reenvío</span>
        <Spot on={highlight === "add"}>
          <span className="rounded-md border border-line bg-surface-raised px-2 py-1 text-text">
            Agregar una dirección de reenvío
          </span>
        </Spot>
      </div>
      <div className="mt-4 flex flex-col gap-2">
        <Bar width="58%" className="bg-surface-raised" />
        <Bar width="40%" className="bg-surface-raised" />
      </div>
    </GmailFrame>
  );
}

/** The small window that asks for the address, filled in, «Siguiente» lit. */
export function AddAddressMock({ address }: { address: string }) {
  return (
    <GmailFrame>
      <div className="mx-auto max-w-72 rounded-lg border border-line bg-surface-raised p-3 shadow-lg shadow-black/40">
        <Bar width="62%" />
        <span className="mt-3 block truncate rounded border border-accent/50 bg-ink px-2 py-1.5 font-mono text-[0.625rem] text-text">
          {address}
        </span>
        <div className="mt-3 flex items-center justify-end gap-3">
          <Bar width="18%" />
          <Spot on>
            <span className="rounded-md bg-accent px-3 py-1 font-semibold text-accent-ink">
              Siguiente
            </span>
          </Spot>
        </div>
      </div>
    </GmailFrame>
  );
}

/**
 * The two options under «Reenvío» once the address is verified: the one to
 * leave as it is, and the one that would send everything.
 */
export function ForwardingChoiceMock() {
  return (
    <div
      aria-hidden
      className="flex w-full select-none flex-col gap-2 rounded-xl border border-line bg-ink p-3 text-xs"
    >
      <span className="flex items-center gap-2.5 rounded-lg border border-incoming/35 bg-incoming/8 px-3 py-2">
        <span className="grid size-3.5 shrink-0 place-items-center rounded-full border-2 border-incoming">
          <span className="size-1.5 rounded-full bg-incoming" />
        </span>
        <span className="min-w-0 flex-1 text-text">Inhabilitar el reenvío</span>
        <Check className="size-3.5 shrink-0 text-incoming" strokeWidth={3} />
      </span>
      <span className="flex items-center gap-2.5 rounded-lg border border-line px-3 py-2 opacity-80">
        <span className="size-3.5 shrink-0 rounded-full border-2 border-line" />
        <span className="min-w-0 flex-1 text-muted line-through decoration-outgoing/60">
          Reenviar una copia del correo entrante a…
        </span>
        <X className="size-3.5 shrink-0 text-outgoing" strokeWidth={3} />
      </span>
    </div>
  );
}
