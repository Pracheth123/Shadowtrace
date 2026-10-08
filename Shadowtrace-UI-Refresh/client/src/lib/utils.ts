import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Merge class names, with later Tailwind utilities winning over earlier ones.
 *
 * Plain `clsx` would leave both `px-4` and `px-2` on the element and let CSS
 * source order decide, which makes a component's `className` prop unreliable.
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
