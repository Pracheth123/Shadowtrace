import {
  createContext,
  useCallback,
  useContext,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";

import { cn } from "@/lib/utils";

/**
 * Tabs — the saved default: a rounded bar with the selected tab highlighted.
 *
 * Hand-rolled rather than pulled from a headless library, but it implements the
 * real tab pattern: one tab in the focus order, arrow keys to move between
 * them, Home/End to jump, and `aria-controls`/`aria-labelledby` wiring. A
 * div-with-onClick would look identical and be unusable by keyboard.
 */

type TabsContextValue = {
  value: string;
  setValue: (value: string) => void;
  baseId: string;
  register: (value: string, node: HTMLButtonElement | null) => void;
  focusRelative: (from: string, delta: number | "first" | "last") => void;
};

const TabsContext = createContext<TabsContextValue | null>(null);

function useTabs(part: string) {
  const context = useContext(TabsContext);
  if (!context) throw new Error(`<${part}> must be used inside <Tabs>`);
  return context;
}

export type TabsProps = React.ComponentPropsWithoutRef<"div"> & {
  defaultValue?: string;
  value?: string;
  onValueChange?: (value: string) => void;
};

export function Tabs({
  className,
  defaultValue = "",
  value: controlled,
  onValueChange,
  ...props
}: TabsProps) {
  const [uncontrolled, setUncontrolled] = useState(defaultValue);
  const value = controlled ?? uncontrolled;
  const baseId = useId();
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
    (tabValue: string, node: HTMLButtonElement | null) => {
      if (node) {
        nodes.current.set(tabValue, node);
        if (!order.current.includes(tabValue)) order.current.push(tabValue);
      } else {
        nodes.current.delete(tabValue);
        order.current = order.current.filter((item) => item !== tabValue);
      }
    },
    [],
  );

  const focusRelative = useCallback(
    (from: string, delta: number | "first" | "last") => {
      const list = order.current;
      if (list.length === 0) return;
      let index: number;
      if (delta === "first") index = 0;
      else if (delta === "last") index = list.length - 1;
      else {
        const current = list.indexOf(from);
        index = (current + delta + list.length) % list.length;
      }
      const target = list[index];
      setValue(target);
      nodes.current.get(target)?.focus();
    },
    [setValue],
  );

  const context = useMemo(
    () => ({ value, setValue, baseId, register, focusRelative }),
    [value, setValue, baseId, register, focusRelative],
  );

  return (
    <TabsContext.Provider value={context}>
      <div className={cn("flex w-full flex-col gap-3", className)} {...props} />
    </TabsContext.Provider>
  );
}

export function TabsList({
  className,
  ...props
}: React.ComponentPropsWithoutRef<"div">) {
  return (
    <div
      role="tablist"
      className={cn(
        "inline-flex w-fit items-center gap-1 rounded-lg border border-border bg-muted p-1",
        className,
      )}
      {...props}
    />
  );
}

export type TabsTabProps = React.ComponentPropsWithoutRef<"button"> & {
  value: string;
};

export function TabsTab({ className, value, ...props }: TabsTabProps) {
  const { value: active, setValue, baseId, register, focusRelative } =
    useTabs("TabsTab");
  const selected = active === value;

  return (
    <button
      type="button"
      role="tab"
      id={`${baseId}-tab-${value}`}
      aria-controls={`${baseId}-panel-${value}`}
      aria-selected={selected}
      // Only the active tab is tabbable; arrows move within the list.
      tabIndex={selected ? 0 : -1}
      ref={(node) => register(value, node)}
      onClick={() => setValue(value)}
      onKeyDown={(event) => {
        const moves: Record<string, number | "first" | "last"> = {
          ArrowRight: 1,
          ArrowLeft: -1,
          Home: "first",
          End: "last",
        };
        const move = moves[event.key];
        if (move === undefined) return;
        event.preventDefault();
        focusRelative(value, move);
      }}
      className={cn(
        "cursor-pointer rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
        selected
          ? "bg-card text-foreground shadow-sm"
          : "text-muted-foreground hover:text-foreground",
        className,
      )}
      {...props}
    />
  );
}

export type TabsPanelProps = React.ComponentPropsWithoutRef<"div"> & {
  value: string;
};

export function TabsPanel({ className, value, ...props }: TabsPanelProps) {
  const { value: active, baseId } = useTabs("TabsPanel");
  if (active !== value) return null;
  return (
    <div
      role="tabpanel"
      id={`${baseId}-panel-${value}`}
      aria-labelledby={`${baseId}-tab-${value}`}
      tabIndex={0}
      className={cn("rounded-lg outline-none", className)}
      {...props}
    />
  );
}
