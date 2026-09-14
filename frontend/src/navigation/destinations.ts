/**
 * Where the shell can take somebody, and which of those the phone's bar can
 * actually fit.
 *
 * Its own module because of the rule at the bottom: for a while the bar
 * listed five of these and nothing else linked to the other two, so Reportes
 * and Comercios existed, worked, and could only be reached on a phone by
 * typing the address. The split is data, the rule is testable, and the shell
 * only draws it.
 */

import {
  ArrowLeftRight,
  BarChart3,
  LayoutGrid,
  Receipt,
  Settings,
  Store,
  Wallet,
} from "lucide-react";
import type { ComponentType } from "react";

export type Destination = {
  label: string;
  icon: ComponentType<{ className?: string }>;
  /** Absent until the screen exists — rendered as pending, never as a dead link. */
  to?:
    | "/"
    | "/transacciones"
    | "/cuentas"
    | "/facturas"
    | "/comercios"
    | "/reportes"
    | "/perfil"
    | "/conectar"
    | "/guias";
};

/** Everything, in the order the rail lists it. */
export const DESTINATIONS: Destination[] = [
  { label: "Resumen", icon: LayoutGrid, to: "/" },
  { label: "Transacciones", icon: ArrowLeftRight, to: "/transacciones" },
  { label: "Cuentas", icon: Wallet, to: "/cuentas" },
  // Con las pantallas del dinero y no al final: una factura es un gasto que
  // todavía no ocurrió, y se busca donde se buscan los gastos.
  { label: "Facturas", icon: Receipt, to: "/facturas" },
  { label: "Reportes", icon: BarChart3, to: "/reportes" },
  { label: "Comercios", icon: Store, to: "/comercios" },
  { label: "Configuración", icon: Settings },
];

/**
 * How many the bottom bar shows beside the action.
 *
 * Three, measured rather than chosen: at 320px a fourth leaves each label
 * about 60px, which is where "Transacciones" stops being a word.
 */
export const BAR_SIZE = 3;

/** What the bar shows, in thumb order. */
export const BAR: Destination[] = DESTINATIONS.slice(0, BAR_SIZE);

/** What it cannot fit, and the sheet therefore has to carry. */
export const OVERFLOW: Destination[] = DESTINATIONS.slice(BAR_SIZE);
