import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/inter";
import "./styles/app.css";
import App from "./App";
import { applyTheme, storedTheme } from "./lib/theme";

applyTheme(storedTheme()); // before the first paint, so a dark choice doesn't flash white

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
