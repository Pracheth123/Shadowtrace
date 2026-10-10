import { cn } from "@/lib/utils";

/**
 * Card — the saved default: rounded corners, a subtle border, a lifted surface.
 *
 * Part names follow the Coss UI snippet (`CardPanel`, not `CardContent`) so the
 * saved example code compiles unchanged.
 */
export function Card({
  className,
  ...props
}: React.ComponentPropsWithoutRef<"div">) {
  return (
    <div
      className={cn(
        "flex flex-col rounded-lg border border-border bg-card text-card-foreground",
        className,
      )}
      {...props}
    />
  );
}

export function CardHeader({
  className,
  ...props
}: React.ComponentPropsWithoutRef<"div">) {
  return (
    <div className={cn("flex flex-col gap-1.5 p-5 pb-3", className)} {...props} />
  );
}

export function CardTitle({
  className,
  ...props
}: React.ComponentPropsWithoutRef<"h3">) {
  return <h3 className={cn("text-lg leading-tight", className)} {...props} />;
}

export function CardDescription({
  className,
  ...props
}: React.ComponentPropsWithoutRef<"p">) {
  return (
    <p
      className={cn("text-sm text-muted-foreground", className)}
      {...props}
    />
  );
}

export function CardPanel({
  className,
  ...props
}: React.ComponentPropsWithoutRef<"div">) {
  return <div className={cn("p-5 pt-0", className)} {...props} />;
}

export function CardFooter({
  className,
  ...props
}: React.ComponentPropsWithoutRef<"div">) {
  return (
    <div
      className={cn(
        "mt-auto flex items-center gap-2 border-t border-border p-5 py-3",
        className,
      )}
      {...props}
    />
  );
}
