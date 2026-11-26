/**
 * One icon per shipped category, so a list can be scanned by shape before it
 * is read.
 *
 * Keyed by value, never by label, for the same reason as the labels in
 * `./categories`. A category this person wrote has no icon anyone could pick
 * for them, so it gets the plain tag every unknown value gets.
 */

import {
  ArrowLeftRight,
  Banknote,
  Bus,
  CircleDashed,
  Fuel,
  GraduationCap,
  HeartPulse,
  Lightbulb,
  type LucideIcon,
  Plane,
  Receipt,
  Repeat,
  Shapes,
  ShoppingBag,
  ShoppingBasket,
  Tag,
  Ticket,
  Utensils,
} from "lucide-react";

const CATEGORY_ICONS: Record<string, LucideIcon> = {
  uncategorized: CircleDashed,
  groceries: ShoppingBasket,
  restaurants: Utensils,
  transport: Bus,
  fuel: Fuel,
  shopping: ShoppingBag,
  entertainment: Ticket,
  subscriptions: Repeat,
  utilities: Lightbulb,
  health: HeartPulse,
  education: GraduationCap,
  travel: Plane,
  fees: Receipt,
  transfers: ArrowLeftRight,
  income: Banknote,
  other: Shapes,
};

export function categoryIcon(value: string | null | undefined): LucideIcon {
  if (value === null || value === undefined) return CircleDashed;
  return CATEGORY_ICONS[value] ?? Tag;
}

/** The icon a transfer between one's own accounts wears in place of a category. */
export const TRANSFER_ICON: LucideIcon = ArrowLeftRight;
