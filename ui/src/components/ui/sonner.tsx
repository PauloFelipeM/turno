import { Toaster as Sonner, type ToasterProps } from "sonner";
import { useTheme } from "@/theme";

// sonner's Toaster with the panel's theme and colours.
export function Toaster(props: ToasterProps) {
  const { resolved } = useTheme();
  return (
    <Sonner
      theme={resolved}
      richColors
      closeButton
      position="bottom-right"
      style={{ "--normal-bg": "var(--popover)", "--normal-text": "var(--popover-foreground)", "--normal-border": "var(--border)" } as React.CSSProperties}
      {...props}
    />
  );
}
