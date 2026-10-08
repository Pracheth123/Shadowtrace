import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
} from "react";

import { cn } from "@/lib/utils";

/**
 * Radio group primitives, shaped like the saved snippet
 * (`RadioGroupPrimitive` + `RadioPrimitive.Root`) so that code compiles here.
 *
 * Implemented as a real radiogroup: one stop in the tab order, arrow keys to
 * move between options, and selection following focus — which is what the
 * pattern specifies for radios, unlike tabs where it is optional.
 */

type RadioContextValue = {
  value: string;
  setValue: (value: string) => void;
  name: string;
  register: (value: string, node: HTMLButtonElement | null) => void;
  move: (from: string, delta: number) => void;
};

const RadioContext = createContext<RadioContextValue | null>(null);

export type RadioGroupPrimitiveProps =
  React.ComponentPropsWithoutRef<"div"> & {
    defaultValue?: string;
    value?: string;
    onValueChange?: (value: string) => void;
    name?: string;
  };

export function RadioGroupPrimitive({
  className,
  defaultValue = "",
  value: controlled,
  onValueChange,
  name = "radio-group",
  ...props
}: RadioGroupPrimitiveProps) {
  const [uncontrolled, setUncontrolled] = useState(defaultValue);
  const value = controlled ?? uncontrolled;
  const order = useRef<string[]>([]);
  const nodes = useRef(new Map<string, HTMLButtonElement>());

  const setValue = useCallback(
    (next: string) => {
      if (controlled === undefined) setUncontrolled(next);
      onValueChange?.(next);
    },
    [controlled, onValueChange],
  );

  const register = useCallback(
    (itemValue: string, node: HTMLButtonElement | null) => {
      if (node) {
        nodes.current.set(itemValue, node);
        if (!order.current.includes(itemValue)) order.current.push(itemValue);
      } else {
        nodes.current.delete(itemValue);
        order.current = order.current.filter((item) => item !== itemValue);
      }
    },
    [],
  );

  const move = useCallback(
    (from: string, delta: number) => {
      const list = order.current.filter((item) => !nodes.current.get(item)?.disabled);
      if (list.length === 0) return;
      const next =
        list[(list.indexOf(from) + delta + list.length) % list.length];
      setValue(next);
      nodes.current.get(next)?.focus();
    },
    [setValue],
  );

  const context = useMemo(
    () => ({ value, setValue, name, register, move }),
    [value, setValue, name, register, move],
  );

  return (
    <RadioContext.Provider value={context}>
      <div role="radiogroup" className={cn(className)} {...props} />
    </RadioContext.Provider>
  );
}

export type RadioRootProps = React.ComponentPropsWithoutRef<"button"> & {
  value: string;
};

function RadioRoot({ className, value, children, ...props }: RadioRootProps) {
  const context = useContext(RadioContext);
  if (!context) {
    throw new Error("<RadioPrimitive.Root> must be used inside a radio group");
  }
  const { value: active, setValue, register, move } = context;
  const checked = active === value;

  return (
    <button
      type="button"
      role="radio"
      aria-checked={checked}
      // `data-checked` is what the segmented-control variants style against.
      data-checked={checked ? "" : undefined}
      tabIndex={checked ? 0 : -1}
      ref={(node) => register(value, node)}
      onClick={() => setValue(value)}
      onKeyDown={(event) => {
        const delta =
          event.key === "ArrowRight" || event.key === "ArrowDown"
            ? 1
            : event.key === "ArrowLeft" || event.key === "ArrowUp"
              ? -1
              : 0;
        if (!delta) return;
        event.preventDefault();
        move(value, delta);
      }}
      className={cn(className)}
      {...props}
    >
      {children}
    </button>
  );
}

export const RadioPrimitive = { Root: RadioRoot };
