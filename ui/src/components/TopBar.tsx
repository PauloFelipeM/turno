import { MonitorIcon, MoonIcon, RotateCwIcon, SettingsIcon, SunIcon } from "lucide-react";
import { toast } from "sonner";
import { send, type Overview } from "@/api";
import { Confirm } from "@/components/Confirm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useI18n } from "@/i18n";
import { useTheme, type ThemeChoice } from "@/theme";

interface Props {
  overview: Overview | null;
  onSettings: () => void;
}

export function TopBar({ overview, onSettings }: Props) {
  const { t } = useI18n();
  const { choice, resolved, setChoice } = useTheme();
  const update = overview?.update;
  const ThemeIcon = choice === "system" ? MonitorIcon : resolved === "dark" ? MoonIcon : SunIcon;
  return (
    <header className="sticky top-0 z-40 border-b bg-background/85 backdrop-blur supports-[backdrop-filter]:bg-background/70">
      <div className="mx-auto flex h-14 max-w-7xl items-center gap-3 px-4">
        <span className="flex items-baseline gap-2">
          <span className="text-lg font-semibold tracking-tight">{t("app.name")}</span>
          {overview ? (
            <span className="text-xs text-muted-foreground tabular">{t("footer.version", { version: overview.version || "?" })}</span>
          ) : null}
        </span>
        {update?.newer ? (
          <Badge variant="warning" className="hidden max-w-[24rem] truncate sm:inline-flex" data-update={update.latest ?? ""}>
            {t("footer.update", { latest: update.latest })}
          </Badge>
        ) : null}
        <div className="ml-auto flex items-center gap-1">
          <Confirm
            data-action="restart"
            title={t("footer.restart")}
            description={t("footer.restartConfirm")}
            confirm={t("footer.restart")}
            tooltip={t("footer.restart")}
            action={async () => {
              const res = await send<{ ok: true; service: boolean }>("/api/restart");
              toast.success(t(res.service ? "footer.restarting" : "footer.restartNoService"));
            }}
            trigger={
              <Button variant="ghost" size="sm" aria-label={t("footer.restart")} data-action="restart-open">
                <RotateCwIcon />
                <span className="hidden sm:inline">{t("footer.restart")}</span>
              </Button>
            }
          />
          <DropdownMenu>
            <Tooltip>
              <TooltipTrigger asChild>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon" aria-label={t("app.theme")} data-action="theme">
                    <ThemeIcon />
                  </Button>
                </DropdownMenuTrigger>
              </TooltipTrigger>
              <TooltipContent>{t("app.theme")}</TooltipContent>
            </Tooltip>
            <DropdownMenuContent align="end">
              <DropdownMenuRadioGroup value={choice} onValueChange={(v) => setChoice(v as ThemeChoice)}>
                {(["light", "dark", "system"] as const).map((c) => (
                  <DropdownMenuRadioItem key={c} value={c} data-theme-choice={c}>
                    {t(`app.themes.${c}`)}
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </DropdownMenuContent>
          </DropdownMenu>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="outline" size="sm" onClick={onSettings} aria-label={t("settings.open")} data-action="settings-open">
                <SettingsIcon />
                <span className="hidden sm:inline">{t("settings.open")}</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent>{t("settings.open")}</TooltipContent>
          </Tooltip>
        </div>
      </div>
      {update?.newer ? (
        <p className="border-t px-4 py-1.5 text-center text-xs text-muted-foreground sm:hidden" data-update={update.latest ?? ""}>
          {t("footer.update", { latest: update.latest })}
        </p>
      ) : null}
    </header>
  );
}
