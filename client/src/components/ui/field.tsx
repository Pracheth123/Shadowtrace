import { createContext, useContext, useId } from "react";

import { cn } from "@/lib/utils";

/**
 * Field / FieldLabel, matching the saved Card snippet.
 *
 * The Field generates one id and hands it to both the label and the control,
 * so `<FieldLabel>Name</FieldLabel>` actually points at its input. A bare
 * <label> with no htmlFor looks right and announces nothing.
 */

const FieldContext = createContext<string | null>(null);

export function useFieldId() {
  return useContext(FieldContext);
}

export function Field({
  className,
  children,
  ...props
}: React.ComponentPropsWithoutRef<"div">) {
  const id = useId();
  return (
    <FieldContext.Provider value={id}>
      <div className={cn("flex flex-col gap-1.5", className)} {...props}>
        {children}
      </div>
    </FieldContext.Provider>
  );
}

export function FieldLabel({
  className,
  htmlFor,
  ...props
}: React.ComponentPropsWithoutRef<"label">) {
  const id = useFieldId();
  return (
    <label
      htmlFor={htmlFor ?? id ?? undefined}
      className={cn("text-sm font-medium text-foreground", className)}
      {...props}
    />
  );
}

export function FieldDescription({
  className,
  ...props
}: React.ComponentPropsWithoutRef<"p">) {
  return (
    <p className={cn("text-xs text-muted-foreground", className)} {...props} />
  );
}

export function Form({
  className,
  ...props
}: React.ComponentPropsWithoutRef<"form">) {
  return <form className={cn("flex flex-col gap-4", className)} {...props} />;
}
