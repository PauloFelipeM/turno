import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/inter";
import "./index.css";
import { App } from "./App";
import { TooltipProvider } from "@/components/ui/tooltip";
import { I18nProvider } from "@/i18n";
import { applyStoredTheme, ThemeProvider } from "@/theme";

applyStoredTheme();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <I18nProvider>
        <TooltipProvider delayDuration={300}>
          <App />
        </TooltipProvider>
      </I18nProvider>
    </ThemeProvider>
  </StrictMode>,
);
