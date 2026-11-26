import type { Config } from "tailwindcss";

// Same system as the AynPulse app (logical utilities for RTL), with Scanner's own accent.
export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: "#14182b", soft: "#424862", mute: "#6f7590" },
        paper: { DEFAULT: "#f5f6f9", raised: "#ffffff", sunk: "#eaecf2" },
        line: "#d9dce6",
        brand: { DEFAULT: "#2b4fd6", strong: "#1c37a0", tint: "#e6ebfc" },
        signal: { pos: "#1f7a4d", neg: "#b4372f", warn: "#a86a10", info: "#2d5d8a" },
      },
      fontFamily: {
        sans: ["system-ui", "-apple-system", "Segoe UI", "Roboto", "Noto Sans", "Noto Sans Arabic",
               "Tahoma", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      borderRadius: { DEFAULT: "8px" },
    },
  },
  plugins: [],
} satisfies Config;
