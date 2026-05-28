import type { Config } from "tailwindcss";
import forms from "@tailwindcss/forms";

const config: Config = {
  content: ["./app/**/*.{js,ts,jsx,tsx}", "./components/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#15191f",
        muted: "#69717d",
        line: "#d9dee5",
        panel: "#f7f8fa",
        accent: "#2f6fed",
        good: "#18815b",
        warn: "#a45c12",
        bad: "#b42318",
      },
      boxShadow: {
        soft: "0 16px 40px rgba(21, 25, 31, 0.08)",
      },
    },
  },
  plugins: [forms],
};

export default config;
