/**
 * What a bill looks like, decided from what it is rather than from its order.
 *
 * A grid of identical cards is a grid nobody scans: the eye has nothing to
 * anchor on, so finding "the gym one" means reading every tile. An icon and a
 * hue drawn from the category fix that without adding a single word — and
 * because both come from the data, the same bill looks the same every time it
 * is drawn, on every screen that ever draws it.
 *
 * Kept apart from the screen for the reason `schedule.ts` is: a table of
 * sixteen categories is exactly the kind of thing that grows a wrong row, and
 * a wrong row here is a gym with a plane on it.
 *
 * **Four hues and no more.** The palette is the app's own — magenta, cyan,
 * green, violet — because a distinct colour per category would be sixteen
 * colours, which is a rainbow, which is the opposite of what the rest of this
 * app looks like.
 */

import {
  ArrowLeftRight,
  Banknote,
  Bus,
  Clapperboard,
  Fuel,
  GraduationCap,
  HeartPulse,
  Landmark,
  Package,
  Plane,
  Receipt,
  Repeat,
  ShoppingBag,
  ShoppingBasket,
  UtensilsCrossed,
  Zap,
} from "lucide-react";
import type { ComponentType } from "react";
import type { Glow } from "@/components/ui/Card";

export type BillLook = {
  icon: ComponentType<{ className?: string }>;
  /** Tint for the badge. */
  badge: string;
  /** The hue the whole card lights up in on hover. */
  glow: Glow;
};

const LOOKS = {
  accent: { badge: "bg-accent/12 text-accent", glow: "accent" },
  cyan: { badge: "bg-cyan/12 text-cyan", glow: "cyan" },
  green: { badge: "bg-incoming/12 text-incoming", glow: "green" },
  violet: { badge: "bg-violet/12 text-violet", glow: "violet" },
} as const satisfies Record<string, Omit<BillLook, "icon">>;

type Hue = keyof typeof LOOKS;

/**
 * The shipped vocabulary, and only it.
 *
 * Every row here is a value `GET /merchants/categories` actually answers —
 * checked against it rather than guessed. Rows for categories that do not
 * exist are rows nothing can ever reach, and they read as if the feature
 * supported something it does not: the first draft of this table had
 * `housing`, `internet` and `gym`, none of which this app ships. Worth
 * knowing: there is **no category for rent**, which is the largest fixed
 * charge most people have, so it lands in `other` until somebody writes one
 * of their own.
 *
 * `GET /merchants/categories` is the authority on what exists; this only
 * decides how the ones it ships are drawn. A category added on the server
 * after this bundle was built falls through to the default, exactly like a
 * category somebody wrote for themselves — which is the point of having a
 * default rather than a lookup that can fail.
 */
const BY_CATEGORY: Record<string, { icon: BillLook["icon"]; hue: Hue }> = {
  groceries: { icon: ShoppingBasket, hue: "green" },
  restaurants: { icon: UtensilsCrossed, hue: "accent" },
  transport: { icon: Bus, hue: "cyan" },
  fuel: { icon: Fuel, hue: "cyan" },
  shopping: { icon: ShoppingBag, hue: "accent" },
  entertainment: { icon: Clapperboard, hue: "violet" },
  subscriptions: { icon: Repeat, hue: "violet" },
  utilities: { icon: Zap, hue: "cyan" },
  health: { icon: HeartPulse, hue: "green" },
  education: { icon: GraduationCap, hue: "violet" },
  travel: { icon: Plane, hue: "cyan" },
  fees: { icon: Landmark, hue: "accent" },
  transfers: { icon: ArrowLeftRight, hue: "cyan" },
  income: { icon: Banknote, hue: "green" },
  other: { icon: Package, hue: "violet" },
};

const DEFAULT: { icon: BillLook["icon"]; hue: Hue } = {
  icon: Receipt,
  hue: "accent",
};

/**
 * How to draw a bill.
 *
 * Income overrides the category outright: what a reader has to tell apart at
 * a glance in this grid is money coming in from money going out, and that
 * distinction beats "this salary is filed under income" every time.
 */
export function lookOf({
  category,
  direction,
}: {
  category: string | null;
  direction: "outgoing" | "incoming";
}): BillLook {
  if (direction === "incoming") {
    return { icon: Banknote, ...LOOKS.green };
  }

  const found = (category !== null && BY_CATEGORY[category]) || DEFAULT;

  return { icon: found.icon, ...LOOKS[found.hue] };
}
