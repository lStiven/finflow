/**
 * A figure that counts up once, on arrival.
 *
 * The rule everywhere else is that money stays the decimal string the API
 * sent, because a float loses cents. This bends it for the frames in between
 * and only those: the animation interpolates a float, but the moment it lands
 * — and every frame after, and every frame for anyone who asked for reduced
 * motion — the component renders the original string through `Money`. Nothing
 * anybody reads at rest has been through a float.
 */

import { useEffect, useState } from "react";
import { Money } from "@/components/Money";
import { useReducedMotion } from "@/lib/useReducedMotion";

const DURATION = 620;

type Props = {
  amount: string;
  currency: string;
  signed?: boolean;
  tone?: "positive" | "negative" | "neutral" | "plain";
  size?: "lg" | "md" | "sm";
  className?: string;
};

export function CountUpMoney({ amount, ...rest }: Props) {
  const target = Number(amount);
  const reduced = useReducedMotion();
  const animatable = Number.isFinite(target) && !reduced;

  /*
   * Starts at zero rather than null. The effect only runs after the first
   * paint, so seeding this with the real amount would show the final figure,
   * snap back to zero and count up to it again.
   */
  const [shown, setShown] = useState<string | null>(animatable ? "0" : null);

  useEffect(() => {
    if (!animatable) {
      setShown(null);
      return;
    }

    let frame = 0;
    const started = performance.now();

    const tick = (now: number) => {
      const progress = Math.min(1, (now - started) / DURATION);
      // Ease out cubic: fast at first, so the figure is readable early and
      // only the last digits settle.
      const eased = 1 - (1 - progress) ** 3;
      if (progress < 1) {
        setShown((target * eased).toFixed(2));
        frame = requestAnimationFrame(tick);
      } else {
        setShown(null);
      }
    };

    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [target, animatable]);

  return <Money amount={shown ?? amount} {...rest} />;
}
