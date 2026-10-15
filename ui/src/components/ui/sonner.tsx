import { Toaster as Sonner, type ToasterProps } from "sonner";
import { useI18n } from "@/i18n";
import { useTheme } from "@/theme";

// sonner's Toaster with the panel's theme and colours.
export function Toaster(props: ToasterProps) {
  const { resolved } = useTheme();
  const { t } = useI18n();
  return (
    <Sonner
      theme={resolved}
      richColors
      closeButton
      containerAriaLabel={t("app.notifications")}
      toastOptions={{ closeButtonAriaLabel: t("app.closeToast") }}
      position="bottom-right"
      style={{ "--normal-bg": "var(--popover)", "--normal-text": "var(--popover-foreground)", "--normal-border": "var(--border)" } as React.CSSProperties}
      {...props}
    />
  );
}
