/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      fontFamily: {
        display: ["'Space Grotesk'", "system-ui", "sans-serif"],
        sans: ["'IBM Plex Sans'", "system-ui", "sans-serif"],
        mono: ["'IBM Plex Mono'", "ui-monospace", "monospace"],
      },
      colors: {
        // Dark hero / nav
        navy: { DEFAULT: "#0a1018", 800: "#111a25", 700: "#1d2a36" },
        // Light body
        surface: "#f5f6f8",
        ink: { DEFAULT: "#13181f", 600: "#56616e", 500: "#6b7683", 400: "#8a95a2" },
        line: { DEFAULT: "#e2e6ea", soft: "#eef1f3" },
        // Accent — cyan/teal. `brand` keeps its existing name so the
        // component classes from the earlier styling pass pick it up.
        brand: {
          50: "#e6f6f8",
          100: "#c2e6ec",
          300: "#67e3f5",
          400: "#22d3ee",
          500: "#22d3ee",
          600: "#0f7f8c",
          700: "#0b5c66",
        },
        coral: "#ff8f6b",
      },
      keyframes: {
        live: { "0%,100%": { opacity: "1" }, "50%": { opacity: ".25" } },
        sweep: { to: { transform: "rotate(360deg)" } },
        pulseDot: { "0%": { transform: "scale(1)", opacity: "1" }, "100%": { transform: "scale(2.2)", opacity: "0" } },
      },
      animation: {
        live: "live 1.8s ease-in-out infinite",
        sweep: "sweep 3s linear infinite",
        pulseDot: "pulseDot 1.8s ease-out infinite",
      },
    },
  },
  plugins: [],
};
