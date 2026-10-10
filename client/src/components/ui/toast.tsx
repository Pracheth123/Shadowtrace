import { useEffect, useState } from "react";
import { XIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Toast — imperative API, matching the saved snippet's
 * `toastManager.add({ title, description })`.
 *
 * The manager lives outside React so any module can raise a toast without
 * threading a hook through the tree; <Toaster /> subscribes to it. Mount
 * <Toaster /> once, near the app root.
 */

export type ToastTone = "default" | "success" | "error";

export type ToastInput = {
  title: string;
  description?: string;
  tone?: ToastTone;
  /** Milliseconds before auto-dismiss. 0 keeps it until dismissed. */
  duration?: number;
};

type ToastRecord = ToastInput & { id: number };

type Listener = (toasts: ToastRecord[]) => void;

class ToastManager {
  private toasts: ToastRecord[] = [];
  private listeners = new Set<Listener>();
  private nextId = 1;

  subscribe(listener: Listener) {
    this.listeners.add(listener);
    listener(this.toasts);
    return () => {
      this.listeners.delete(listener);
    };
  }

  add(input: ToastInput) {
    const toast: ToastRecord = { duration: 4000, tone: "default", ...input, id: this.nextId++ };
    this.toasts = [...this.toasts, toast];
    this.emit();
    return toast.id;
  }

  dismiss(id: number) {
    this.toasts = this.toasts.filter((toast) => toast.id !== id);
    this.emit();
  }

  private emit() {
    for (const listener of this.listeners) listener(this.toasts);
  }
}

export const toastManager = new ToastManager();

const toneClasses: Record<ToastTone, string> = {
  default: "border-border",
  success: "border-success/40",
  error: "border-destructive/40",
};

function ToastItem({ toast }: { toast: ToastRecord }) {
  useEffect(() => {
    if (!toast.duration) return;
    const timer = window.setTimeout(
      () => toastManager.dismiss(toast.id),
      toast.duration,
    );
    return () => window.clearTimeout(timer);
  }, [toast.id, toast.duration]);

  return (
    <li
      className={cn(
        "pointer-events-auto flex w-80 max-w-[calc(100vw-2rem)] animate-toast-in items-start gap-3 rounded-md border border-l-4 border-l-[var(--charcoal)] bg-card p-3 shadow-card",
        toneClasses[toast.tone ?? "default"],
      )}
    >
      <div className="flex-1">
        <p className="text-sm font-medium">{toast.title}</p>
        {toast.description && (
          <p className="mt-0.5 text-xs text-muted-foreground">
            {toast.description}
          </p>
        )}
      </div>
      <button
        type="button"
        aria-label="Dismiss notification"
        onClick={() => toastManager.dismiss(toast.id)}
        className="shrink-0 rounded text-muted-foreground transition-colors hover:text-foreground"
      >
        <XIcon className="size-4" />
      </button>
    </li>
  );
}

export function Toaster() {
  const [toasts, setToasts] = useState<ToastRecord[]>([]);
  useEffect(() => toastManager.subscribe(setToasts), []);

  return (
    <ul
      // Polite, not assertive: a confirmation should not interrupt whatever a
      // screen reader is already saying mid-interview.
      aria-live="polite"
      className="pointer-events-none fixed inset-x-0 bottom-4 z-50 m-0 flex list-none flex-col items-center gap-2 p-0 sm:inset-x-auto sm:right-4 sm:items-end"
    >
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} />
      ))}
    </ul>
  );
}
