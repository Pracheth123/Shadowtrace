import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex w-fit shrink-0 items-center gap-1.5 whitespace-nowrap rounded font-medium",
  {
    variants: {
      variant: {
        // The saved choice: a white label with rounded corners.
        default: "border border-border bg-card text-foreground",
        primary: "bg-primary text-primary-foreground",
        secondary: "bg-secondary text-secondary-foreground",
        outline: "border border-border bg-transparent text-foreground",
        muted: "bg-muted text-muted-foreground",
      },
      size: {
        sm: "px-2 py-0.5 text-[11px]",
        default: "px-2.5 py-0.5 text-xs",
        lg: "px-3 py-1 text-sm",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export type BadgeProps = React.ComponentPropsWithoutRef<"span"> &
  VariantProps<typeof badgeVariants>;

export function Badge({ className, variant, size, ...props }: BadgeProps) {
  return (
    <span
      className={cn(badgeVariants({ variant, size }), className)}
      {...props}
    />
  );
}

/**
 * The coloured dot used inside status badges in the history table.
 * Decorative, so it is hidden from assistive tech — the badge text carries
 * the meaning, never the colour alone.
 */
export function BadgeDot({ className }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn("size-1.5 rounded-full bg-muted-foreground/60", className)}
    />
  );
}

export { badgeVariants };
