import { cn } from "@/lib/utils";

export type ProgressProps = Omit<
  React.ComponentPropsWithoutRef<"div">,
  "children"
> & {
  /** 0–100. Clamped, so a bad count never overflows the track. */
  value?: number;
  max?: number;
  /** Optional spoken description, e.g. "3 of 10 questions completed". */
  label?: string;
};

/**
 * Progress — the saved default: a thin line with rounded ends.
 *
 * Uses role="progressbar" with the aria-value* trio rather than a bare div, so
 * a screen reader announces position instead of nothing at all.
 */
export function Progress({
  className,
  value = 0,
  max = 100,
  label,
  ...props
}: ProgressProps) {
  const safeMax = max > 0 ? max : 100;
  const clamped = Math.min(safeMax, Math.max(0, value));
  const percent = (clamped / safeMax) * 100;

  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={safeMax}
      aria-valuenow={clamped}
      aria-valuetext={label}
      aria-label={label}
      className={cn(
        "h-1.5 w-full overflow-hidden rounded-full bg-muted",
        className,
      )}
      {...props}
    >
      <div
        className="h-full rounded-full bg-primary transition-[width] duration-500 ease-out"
        style={{ width: `${percent}%` }}
      />
    </div>
  );
}
