import { cva } from "class-variance-authority";

/**
 * Segmented control styling, kept here (not in the component) so the saved
 * snippet's `segmentedControlRootClassName` / `segmentedControlItemVariants`
 * imports resolve unchanged.
 */

export const segmentedControlRootClassName =
  "inline-flex w-full items-center gap-1 rounded-md border border-border bg-muted p-1";

export const segmentedControlItemVariants = cva(
  "cursor-pointer select-none rounded px-3 py-2 text-center text-sm font-medium transition-[background-color,color,box-shadow] duration-150 disabled:cursor-not-allowed disabled:opacity-55",
  {
    variants: {
      state: {
        // The saved look: the selected option is outlined and lifted.
        checked:
          "text-muted-foreground data-[checked]:bg-charcoal data-[checked]:font-semibold data-[checked]:text-[var(--on-charcoal)] hover:text-foreground data-[checked]:hover:text-[var(--on-charcoal)]",
        plain: "text-muted-foreground hover:text-foreground",
      },
    },
    defaultVariants: { state: "checked" },
  },
);
