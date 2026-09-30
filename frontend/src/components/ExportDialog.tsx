import { useQuery } from "@tanstack/react-query";
import { Download, LoaderCircle, X } from "lucide-react";
import { useCallback, useId, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { downloadExport } from "@/api/export";
import { type Account, type CategoryOption, transactionsQuery } from "@/api/queries";
import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import { Select } from "@/components/ui/Select";
import { cn } from "@/lib/cn";
import {
  EXPORT_DIRECTIONS,
  EXPORT_FORMATS,
  EXPORT_PERIODS,
  type ExportOptions,
  exportErrorMessage,
  exportFilters,
  exportOptionsError,
  initialExportOptions,
  type ListSelection,
  MAX_EXPORT_ROWS,
} from "@/lib/exporting";
import { useDismissOnEscape } from "@/lib/useDismissOnEscape";
import { useScrollLock } from "@/lib/useScrollLock";
import { categoryLabel } from "@/merchants/categories";

type Props = {
  /** What the Transacciones screen has selected, so the dialog starts there. */
  selection: ListSelection;
  accounts: readonly Account[];
  categories: readonly CategoryOption[];
  /** Display names for the filters carried over from the list. */
  merchantName?: string;
  originName?: string;
};

/**
 * "Exportar" beside "Agregar", and the dialog it opens: which movements go
 * into the file — period, kind, account, category, transfers — and in which
 * format. It says how many there are before anything is downloaded, so the
 * ceiling is something the person sees coming rather than a refusal.
 */
export function ExportButton(props: Props) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <Button variant="ghost" aria-haspopup="dialog" onClick={() => setOpen(true)}>
        <Download className="size-4" aria-hidden />
        Exportar
      </Button>
      {/*
        Portalled to <body>: the screen's content sits inside an animated
        container, and a transformed ancestor turns `position: fixed` into
        "fixed to that container" — the dialog would open a page below.
      */}
      {open
        ? createPortal(
            <ExportDialog {...props} onClose={() => setOpen(false)} />,
            document.body,
          )
        : null}
    </>
  );
}

function ExportDialog({
  selection,
  accounts,
  categories,
  merchantName,
  originName,
  onClose,
}: Props & { onClose: () => void }) {
  const [options, setOptions] = useState<ExportOptions>(() =>
    initialExportOptions(selection),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const titleId = useId();
  const transfersId = useId();

  const close = useCallback(() => {
    if (!busy) onClose();
  }, [busy, onClose]);

  useScrollLock(true);
  useDismissOnEscape(close, true);

  const set = (patch: Partial<ExportOptions>) => {
    setError(null);
    setOptions((previous) => ({ ...previous, ...patch }));
  };

  const invalid = exportOptionsError(options);
  const filters = useMemo(() => exportFilters(options), [options]);
  // One row is enough: `total` is what matters, and it is the same count the
  // server will refuse past the ceiling.
  const count = useQuery({
    ...transactionsQuery({ ...filters, limit: 1 }),
    enabled: invalid === null,
  });
  const total = count.data?.total;
  const tooMany = total !== undefined && total > MAX_EXPORT_ROWS;
  const empty = total === 0;

  async function download() {
    setBusy(true);
    setError(null);

    try {
      await downloadExport(filters, options.format);
      onClose();
    } catch (cause) {
      setError(exportErrorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  const carried = [
    options.search
      ? {
          key: "search",
          label: `Búsqueda: «${options.search}»`,
          drop: { search: undefined },
        }
      : null,
    options.merchantId
      ? {
          key: "merchant",
          label: `Comercio: ${merchantName ?? "el elegido"}`,
          drop: { merchantId: undefined },
        }
      : null,
    options.origin
      ? {
          key: "origin",
          label: `Origen: ${originName ?? options.origin}`,
          drop: { origin: undefined },
        }
      : null,
    options.unassigned
      ? {
          key: "unassigned",
          label: "Solo sin asignar",
          drop: { unassigned: undefined },
        }
      : null,
  ].filter((chip) => chip !== null);

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-ink/80 backdrop-blur-sm sm:items-center sm:p-5"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <div
        role="dialog"
        aria-modal
        aria-labelledby={titleId}
        className="rise surface flex max-h-[92dvh] w-full max-w-lg flex-col rounded-t-card border border-line bg-surface sm:rounded-card"
      >
        <header className="flex items-start justify-between gap-3 border-line border-b px-5 pt-5 pb-4">
          <div>
            <h2 id={titleId} className="font-semibold text-lg tracking-tight">
              Exportar movimientos
            </h2>
            <p className="mt-1 text-muted text-sm">
              Elige qué entra en el archivo. Se exportan todos, no solo la página.
            </p>
          </div>
          <Button
            variant="quiet"
            className="-mr-2 px-2 py-2"
            aria-label="Cerrar"
            onClick={close}
            disabled={busy}
          >
            <X className="size-5" aria-hidden />
          </Button>
        </header>

        <div className="flex flex-col gap-5 overflow-y-auto px-5 py-5">
          <Choice
            legend="Periodo"
            options={EXPORT_PERIODS}
            value={options.period}
            onChange={(period) => set({ period })}
          />

          {options.period === "custom" ? (
            <div className="grid grid-cols-2 gap-3">
              <Field
                label="Desde"
                type="date"
                value={options.from}
                max={options.to || undefined}
                onChange={(event) => set({ from: event.target.value })}
              />
              <Field
                label="Hasta"
                type="date"
                value={options.to}
                min={options.from || undefined}
                onChange={(event) => set({ to: event.target.value })}
              />
            </div>
          ) : null}

          <Choice
            legend="Movimientos"
            options={EXPORT_DIRECTIONS}
            value={options.direction}
            onChange={(direction) => set({ direction })}
          />

          <div className="grid gap-4 sm:grid-cols-2">
            <Select
              label="Cuenta"
              placeholder="Todas"
              value={options.accountId}
              onChange={(event) => set({ accountId: event.target.value })}
              options={accounts.map((account) => ({
                value: account.id,
                label: account.name,
              }))}
            />
            <Select
              label="Categoría"
              placeholder="Todas"
              value={options.category}
              onChange={(event) => set({ category: event.target.value })}
              options={categories.map((option) => ({
                value: option.value,
                label: categoryLabel(option.value, option.label),
              }))}
            />
          </div>

          <div className="flex items-start gap-3 text-sm">
            <input
              id={transfersId}
              type="checkbox"
              className="mt-0.5 size-4 accent-accent"
              aria-describedby={`${transfersId}-hint`}
              checked={options.transfers === "include"}
              onChange={(event) =>
                set({ transfers: event.target.checked ? "include" : "exclude" })
              }
            />
            <div>
              <label htmlFor={transfersId}>Incluir traslados entre mis cuentas</label>
              <p id={`${transfersId}-hint`} className="text-faint text-xs">
                Como pagar la tarjeta desde ahorros. No son gasto ni ingreso, y el
                archivo los marca como «Traslado».
              </p>
            </div>
          </div>

          {carried.length > 0 ? (
            <div className="flex flex-col gap-2">
              <span className="text-muted text-sm">También de la lista</span>
              <div className="flex flex-wrap gap-2">
                {carried.map((chip) => (
                  <span
                    key={chip.key}
                    className="inline-flex items-center gap-1.5 rounded-full border border-line bg-ink py-1 pr-1 pl-3 text-xs"
                  >
                    {chip.label}
                    <button
                      type="button"
                      className="grid size-6 place-items-center rounded-full text-muted hover:bg-surface-raised hover:text-text"
                      aria-label={`Quitar ${chip.label}`}
                      onClick={() => set(chip.drop)}
                    >
                      <X className="size-3.5" aria-hidden />
                    </button>
                  </span>
                ))}
              </div>
            </div>
          ) : null}

          <Choice
            legend="Formato"
            options={EXPORT_FORMATS}
            value={options.format}
            onChange={(format) => set({ format })}
          />
        </div>

        <footer className="flex flex-col gap-3 border-line border-t px-5 pt-4 pb-5">
          <p aria-live="polite" className="text-sm">
            <Summary
              invalid={invalid}
              loading={count.isPending && invalid === null}
              failed={count.isError}
              total={total}
            />
          </p>
          {error ? (
            <p role="alert" className="text-outgoing text-sm">
              {error}
            </p>
          ) : null}
          <Button
            full
            onClick={() => void download()}
            disabled={busy || invalid !== null || tooMany || empty || count.isPending}
          >
            {busy ? (
              <LoaderCircle className="size-4 animate-spin" aria-hidden />
            ) : (
              <Download className="size-4" aria-hidden />
            )}
            {busy
              ? "Generando…"
              : `Descargar ${options.format === "xlsx" ? "Excel" : "CSV"}`}
          </Button>
        </footer>
      </div>
    </div>
  );
}

function Summary({
  invalid,
  loading,
  failed,
  total,
}: {
  invalid: string | null;
  loading: boolean;
  failed: boolean;
  total: number | undefined;
}) {
  if (invalid) return <span className="text-outgoing">{invalid}</span>;
  if (loading) return <span className="text-muted">Contando movimientos…</span>;
  if (failed || total === undefined) {
    return <span className="text-muted">No pudimos contar los movimientos.</span>;
  }
  if (total === 0) {
    return <span className="text-muted">No hay movimientos con estos filtros.</span>;
  }
  if (total > MAX_EXPORT_ROWS) {
    return (
      <span className="text-outgoing">
        Son {total.toLocaleString("es-CO")} movimientos y un archivo lleva hasta{" "}
        {MAX_EXPORT_ROWS.toLocaleString("es-CO")}. Acota las fechas.
      </span>
    );
  }

  return (
    <span>
      Se van a exportar{" "}
      <strong>
        {total.toLocaleString("es-CO")} {total === 1 ? "movimiento" : "movimientos"}
      </strong>
      .
    </span>
  );
}

/** A small segmented choice: one of a few values, all visible at once. */
function Choice<T extends string>({
  legend,
  options,
  value,
  onChange,
}: {
  legend: string;
  options: ReadonlyArray<{ value: T; label: string }>;
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-2 text-muted text-sm">{legend}</legend>
      <div className="flex flex-wrap gap-2">
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            aria-pressed={option.value === value}
            onClick={() => onChange(option.value)}
            className={cn(
              "rounded-full border px-3.5 py-2 text-sm transition-colors duration-150",
              option.value === value
                ? "border-accent bg-accent/15 text-text"
                : "border-line bg-ink text-muted hover:text-text",
            )}
          >
            {option.label}
          </button>
        ))}
      </div>
    </fieldset>
  );
}
