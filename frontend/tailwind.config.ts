import type { Config } from "tailwindcss";

// Use logical utilities (ms-/me-/ps-/pe-/start-/end-) everywhere so Arabic RTL works for free.
export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: "#0f1f1c", soft: "#3d4f4b", mute: "#6b7d79" },
        paper: { DEFAULT: "#f6f5f1", raised: "#ffffff", sunk: "#ecebe5" },
        line: "#dcdad2",
        brand: { DEFAULT: "#0d6b5c", strong: "#08483e", tint: "#e3f1ed" },
        signal: { pos: "#1f7a4d", neg: "#b4372f", warn: "#a86a10", info: "#2d5d8a" },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "var(--font-arabic)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      borderRadius: { DEFAULT: "6px" },
    },
  },
  plugins: [],
} satisfies Config;
