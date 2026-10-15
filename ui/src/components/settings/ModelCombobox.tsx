import { useState } from "react";
import { CheckIcon, ChevronsUpDownIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverAnchor, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useI18n } from "@/i18n";
import { cn } from "@/lib/utils";

/** A model id: free text, with the known models one click away. */
export function ModelCombobox({ value, onChange, known, label, sf }: {
  value: string;
  onChange: (v: string) => void;
  known: string[];
  label: string;
  sf: string;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverAnchor asChild>
        <div className="flex min-w-44 items-center gap-1">
          <Input value={value} onChange={(ev) => onChange(ev.target.value)} aria-label={label} data-sf={sf} className="font-mono text-xs" />
          <PopoverTrigger asChild>
            <Button variant="ghost" size="icon-sm" aria-label={t("settings.pickModel")} data-sf={`${sf}-pick`}>
              <ChevronsUpDownIcon />
            </Button>
          </PopoverTrigger>
        </div>
      </PopoverAnchor>
      <PopoverContent>
        <p className="px-2 py-1.5 text-xs font-medium text-muted-foreground">{t("settings.models")}</p>
        <ul role="listbox" aria-label={t("settings.models")}>
          {known.map((m) => (
            <li key={m} role="option" aria-selected={m === value}>
              <button
                type="button"
                className={cn("flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left font-mono text-xs hover:bg-accent focus-visible:bg-accent focus-visible:outline-none")}
                onClick={() => {
                  onChange(m);
                  setOpen(false);
                }}
                data-model={m}
              >
                <CheckIcon className={cn("size-3.5", m === value ? "opacity-100" : "opacity-0")} />
                {m}
              </button>
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  );
}
