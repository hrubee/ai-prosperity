import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./lib/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // Dark "prosperity" fintech palette — deep ink + gold + signal green/red.
        ink: {
          950: "#0a0b0f",
          900: "#0e1015",
          800: "#15181f",
          700: "#1d212b",
          600: "#272c39",
        },
        gold: {
          400: "#e9c46a",
          500: "#d4a93f",
          600: "#b8902f",
        },
        gain: "#22c55e",
        loss: "#ef4444",
        muted: "#8b93a7",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(212,169,63,0.18), 0 8px 40px -12px rgba(212,169,63,0.25)",
      },
    },
  },
  plugins: [],
};

export default config;
