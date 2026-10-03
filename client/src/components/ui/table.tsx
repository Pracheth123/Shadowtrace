import { cn } from "@/lib/utils";

/**
 * Table parts. The wrapper owns the horizontal scroll so a wide table never
 * makes the whole page scroll sideways.
 */

export function Table({
  className,
  ...props
}: React.ComponentPropsWithoutRef<"table">) {
  return (
    <div className="w-full overflow-x-auto">
      <table
        className={cn("w-full caption-bottom border-collapse text-sm", className)}
        {...props}
      />
    </div>
  );
}

export function TableCaption({
  className,
  ...props
}: React.ComponentPropsWithoutRef<"caption">) {
  return (
    <caption
      className={cn("mt-3 text-left text-xs text-muted-foreground", className)}
      {...props}
    />
  );
}

export function TableHeader({
  className,
  ...props
}: React.ComponentPropsWithoutRef<"thead">) {
  return <thead className={cn("[&_tr]:border-b", className)} {...props} />;
}

export function TableBody({
  className,
  ...props
}: React.ComponentPropsWithoutRef<"tbody">) {
  return (
    <tbody
      className={cn("[&_tr:last-child]:border-0", className)}
      {...props}
    />
  );
}

export function TableFooter({
  className,
  ...props
}: React.ComponentPropsWithoutRef<"tfoot">) {
  return (
    <tfoot
      className={cn("border-t bg-muted/50 font-medium", className)}
      {...props}
    />
  );
}

export function TableRow({
  className,
  ...props
}: React.ComponentPropsWithoutRef<"tr">) {
  return (
    <tr
      className={cn(
        "border-b border-border transition-colors hover:bg-muted/50",
        className,
      )}
      {...props}
    />
  );
}

export function TableHead({
  className,
  ...props
}: React.ComponentPropsWithoutRef<"th">) {
  return (
    <th
      scope="col"
      className={cn(
        "h-10 px-3 text-left align-middle text-xs font-medium text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

export function TableCell({
  className,
  ...props
}: React.ComponentPropsWithoutRef<"td">) {
  return <td className={cn("px-3 py-3 align-middle", className)} {...props} />;
}
