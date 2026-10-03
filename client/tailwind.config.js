/**
 * Panel AI — Tailwind theme.
 *
 * The palette is the Real Time Colors set, declared once as RGB channel
 * triplets in src/styles.css and referenced here through `rgb(var(--x) /
 * <alpha-value>)`. Channels rather than hex is what makes opacity utilities
 * work, so `bg-primary/10` gives a real tint instead of being ignored.
 *
 *   background #fefcfb   text #021016   primary #3d27ce
 *   secondary  #ffdbed   accent #443dff
 *
 * Everything else (border, muted, card, status colours) is derived from those
 * five so the UI stays one system rather than five colours plus ad-hoc greys.
 */

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        background: "rgb(var(--background) / <alpha-value>)",
        foreground: "rgb(var(--foreground) / <alpha-value>)",
        primary: {
          DEFAULT: "rgb(var(--primary) / <alpha-value>)",
          foreground: "rgb(var(--primary-foreground) / <alpha-value>)",
        },
        secondary: {
          DEFAULT: "rgb(var(--secondary) / <alpha-value>)",
          foreground: "rgb(var(--secondary-foreground) / <alpha-value>)",
        },
        accent: {
          DEFAULT: "rgb(var(--accent) / <alpha-value>)",
          foreground: "rgb(var(--accent-foreground) / <alpha-value>)",
        },
        muted: {
          DEFAULT: "rgb(var(--muted) / <alpha-value>)",
          foreground: "rgb(var(--muted-foreground) / <alpha-value>)",
        },
        card: {
          DEFAULT: "rgb(var(--card) / <alpha-value>)",
          foreground: "rgb(var(--card-foreground) / <alpha-value>)",
        },
        border: "rgb(var(--border) / <alpha-value>)",
        input: "rgb(var(--input) / <alpha-value>)",
        ring: "rgb(var(--ring) / <alpha-value>)",
        destructive: {
          DEFAULT: "rgb(var(--destructive) / <alpha-value>)",
          foreground: "rgb(var(--destructive-foreground) / <alpha-value>)",
        },
        success: "rgb(var(--success) / <alpha-value>)",
        warning: "rgb(var(--warning) / <alpha-value>)",
      },
      fontFamily: {
        // Stardom is a display serif — headings only. Body text falls to Inter,
        // which is what keeps long question text readable.
        display: ["Stardom", "Georgia", "serif"],
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "Segoe UI",
          "Helvetica Neue",
          "sans-serif",
        ],
        mono: ["ui-monospace", "SFMono-Regular", "Consolas", "monospace"],
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 0.25rem)",
        sm: "calc(var(--radius) - 0.4rem)",
      },
      boxShadow: {
        card: "0 1px 2px rgb(var(--foreground) / 0.04), 0 8px 24px -12px rgb(var(--foreground) / 0.10)",
        focus: "0 0 0 3px rgb(var(--ring) / 0.35)",
      },
      keyframes: {
        "toast-in": {
          from: { opacity: "0", transform: "translateY(0.5rem) scale(0.98)" },
          to: { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        "pulse-ring": {
          "0%": { boxShadow: "0 0 0 0 rgb(var(--accent) / 0.45)" },
          "70%": { boxShadow: "0 0 0 0.75rem rgb(var(--accent) / 0)" },
          "100%": { boxShadow: "0 0 0 0 rgb(var(--accent) / 0)" },
        },
      },
      animation: {
        "toast-in": "toast-in 160ms ease-out",
        "pulse-ring": "pulse-ring 1.8s ease-out infinite",
      },
    },
  },
  plugins: [],
};
