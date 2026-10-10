import { useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";

// One-time section reveal using a native IntersectionObserver and CSS, so it
// does not wait on the lazily loaded animation features. Only sections that
// start below the fold are ever hidden; anything already on screen, or any
// browser without IntersectionObserver, renders normally. Under reduced motion
// nothing is hidden or faded: sections render static from the start.
export function Reveal({ as = "div", className, children, delay = 0 }: { as?: "div" | "section"; className?: string; children: ReactNode; delay?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<"static" | "pending" | "shown">("static");
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined" || el.getBoundingClientRect().top < window.innerHeight) return;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    setState("pending");
    const observer = new IntersectionObserver(([entry]) => {
      if (entry?.isIntersecting) { setState("shown"); observer.disconnect(); }
    }, { threshold: 0.12 });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  const Tag = as;
  const style = delay ? ({ "--reveal-delay": `${delay}s` } as CSSProperties) : undefined;
  return <Tag ref={ref} className={["reveal", className].filter(Boolean).join(" ")} data-reveal={state} style={style}>{children}</Tag>;
}
