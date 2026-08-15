/** @type {import('tailwindcss').Config} */
// Semantic colours point at CSS variables defined in src/index.css, so the same
// class names render correctly in light and dark. Grok Bot's chrome is
// monochrome (black is the only accent); saturated colour lives only in the bot
// avatars, which are drawn from an explicit palette, not Tailwind.
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        surface: "var(--surface)",
        "surface-2": "var(--surface-2)",
        ink: "var(--ink)",
        "ink-contrast": "var(--ink-contrast)",
        "text-primary": "var(--text-primary)",
        "text-secondary": "var(--text-secondary)",
        hairline: "var(--hairline)",
        "bubble-in": "var(--bubble-in)",
        "bubble-in-text": "var(--bubble-in-text)",
        "bubble-out": "var(--bubble-out)",
        "bubble-out-text": "var(--bubble-out-text)",
        amber: "var(--amber)",
        "amber-bg": "var(--amber-bg)",
        recording: "var(--recording)",
        unread: "var(--unread)",
      },
      borderRadius: {
        bubble: "20px",
        card: "16px",
      },
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          "SF Pro Text",
          "Inter",
          "Segoe UI",
          "Roboto",
          "system-ui",
          "sans-serif",
        ],
      },
    },
  },
  plugins: [],
};
