import { cva } from "class-variance-authority";

/**
 * Segmented control styling, kept here (not in the component) so the saved
 * snippet's `segmentedControlRootClassName` / `segmentedControlItemVariants`
 * imports resolve unchanged.
 */

export const segmentedControlRootClassName =
  "inline-flex w-full items-center gap-1 rounded-lg border border-border bg-muted p-1";

export const segmentedControlItemVariants = cva(
  "cursor-pointer select-none rounded-md px-3 py-1.5 text-center text-sm font-medium transition-[background-color,color,box-shadow]",
  {
    variants: {
      state: {
        // The saved look: the selected option is outlined and lifted.
        checked:
          "text-muted-foreground data-[checked]:bg-card data-[checked]:text-foreground data-[checked]:shadow-sm data-[checked]:ring-1 data-[checked]:ring-border hover:text-foreground",
        plain: "text-muted-foreground hover:text-foreground",
      },
    },
    defaultVariants: { state: "checked" },
  },
);
