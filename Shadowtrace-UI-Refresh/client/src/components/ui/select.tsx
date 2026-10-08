import { ChevronDownIcon } from "lucide-react";

import { useFieldId } from "@/components/ui/field";
import { cn } from "@/lib/utils";

/**
 * Select — a styled native <select>.
 *
 * A deliberate deviation from the saved snippet's compound API
 * (`SelectTrigger`/`SelectPopup`/`SelectItem`). That API needs a headless
 * popover with focus trapping and typeahead to be usable; a native select
 * already has all of it, plus the right mobile picker, for no dependency. The
 * styling matches the rest of the system, which is what the choice was about.
 */

export type SelectItemOption = { label: string; value: string };

export type SelectProps = Omit<
  React.ComponentPropsWithoutRef<"select">,
  "children"
> & {
  items: SelectItemOption[];
};

export function Select({ className, items, id, ...props }: SelectProps) {
  const fieldId = useFieldId();

  return (
    <div className="relative">
      <select
        id={id ?? fieldId ?? undefined}
        className={cn(
          "h-10 w-full cursor-pointer appearance-none rounded-md border border-input bg-card pl-3 pr-9 text-sm text-foreground transition-[border-color,box-shadow]",
          "focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/35",
          "disabled:cursor-not-allowed disabled:opacity-50",
          className,
        )}
        {...props}
      >
        {items.map(({ label, value }) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </select>
      <ChevronDownIcon
        aria-hidden="true"
        className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
      />
    </div>
  );
}
