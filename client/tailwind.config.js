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
        "primary-hover": "rgb(var(--primary-hover) / <alpha-value>)",
        charcoal: "rgb(var(--charcoal-rgb) / <alpha-value>)",
        brass: "rgb(var(--brass-rgb) / <alpha-value>)",
      },
      fontFamily: {
        // Self-hosted in src/assets/fonts (see @font-face in styles.css and
        // docs/DESIGN_SYSTEM.md). Syne: headings and brand. Newsreader: quotes
        // and editorial narrative. Schibsted Grotesk: interface text.
        // JetBrains Mono: short labels and metadata only.
        display: ["Syne", "Schibsted Grotesk", "ui-sans-serif", "system-ui", "sans-serif"],
        serif: ["Newsreader", "Iowan Old Style", "Georgia", "serif"],
        sans: ["Schibsted Grotesk", "ui-sans-serif", "system-ui", "Segoe UI", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Consolas", "monospace"],
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 0.25rem)",
        sm: "calc(var(--radius) - 0.4rem)",
      },
      boxShadow: {
        card: "var(--shadow-raised)",
        paper: "var(--shadow-paper)",
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
