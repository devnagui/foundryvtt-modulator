/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["class", "[data-theme='modulator-dark']"],
  theme: { extend: {} },
  plugins: [require("daisyui")],
  daisyui: {
    themes: [
      {
        "modulator-dark": {
          "primary": "#22c55e",
          "secondary": "#334155",
          "accent": "#0ea5a4",
          "neutral": "#1f2937",
          "base-100": "#111827",
          "base-200": "#0f172a",
          "base-300": "#0b1220",
          "base-content": "#e8eef9",
          "info": "#60a5fa",
          "success": "#34d399",
          "warning": "#fbbf24",
          "error": "#f87171"
        }
      },
      "light"
    ]
  }
};
