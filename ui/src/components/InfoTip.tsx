import { CircleHelpIcon } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useI18n } from "@/i18n";

/** A "?" next to a field label that explains it. A click (not a hover) opens it, so it also works on a phone. */
export function InfoTip({ text }: { text: string }) {
  const { t } = useI18n();
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={t("help.label")}
          className="inline-flex size-4 items-center justify-center rounded-full text-muted-foreground outline-none hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50"
        >
          <CircleHelpIcon className="size-3.5" />
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-80 max-w-[calc(100vw-2rem)] text-xs leading-relaxed">{text}</PopoverContent>
    </Popover>
  );
}
