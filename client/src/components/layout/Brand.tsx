export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <span className="brand">
      <svg className="brand-mark" width="30" height="30" viewBox="0 0 30 30" fill="none" aria-hidden="true">
        <rect x="1" y="1" width="28" height="28" rx="8" fill="currentColor" />
        <path d="M10 9h10M10 15h10M10 21h10" stroke="var(--brand-paper)" strokeWidth="2" strokeLinecap="round" />
        <path d="M7 9h1M22 15h1M7 21h1" stroke="var(--brand-paper)" strokeWidth="2" strokeLinecap="round" />
      </svg>
      {!compact && <span>shadow<span className="brand-light">trace</span><span className="brand-dot">.</span></span>}
    </span>
  );
}
