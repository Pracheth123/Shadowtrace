import { forwardRef } from "react";

import { cn } from "@/lib/utils";
import { useFieldId } from "@/components/ui/field";

export type InputProps = React.ComponentPropsWithoutRef<"input">;

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, type = "text", id, ...props }, ref) => {
    const fieldId = useFieldId();
    return (
    <input
      ref={ref}
      id={id ?? fieldId ?? undefined}
      type={type}
      className={cn(
        "h-10 w-full rounded-md border border-input bg-card px-3 text-sm text-foreground transition-[border-color,box-shadow]",
        "placeholder:text-muted-foreground/70",
        "focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/35 focus-visible:ring-offset-0",
        "disabled:cursor-not-allowed disabled:opacity-50",
        "aria-[invalid=true]:border-destructive aria-[invalid=true]:ring-destructive/30",
        className,
      )}
      {...props}
    />
    );
  },
);
Input.displayName = "Input";
