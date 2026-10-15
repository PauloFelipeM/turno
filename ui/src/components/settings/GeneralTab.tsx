import { TriangleAlertIcon } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { LANGS, useI18n } from "@/i18n";
import type { Draft } from "./draft";

/** Settings → General: the owner's language and ntfy. The ntfy secret itself is never shown or sent. */
export function GeneralTab({ draft, update }: { draft: Draft; update: (fn: (d: Draft) => void) => void }) {
  const { t } = useI18n();
  const g = draft.gen;
  const langs = draft.cfg.languages?.length ? draft.cfg.languages : LANGS;
  return (
    <div className="grid max-w-xl gap-5">
      <div className="grid gap-1.5">
        <Label htmlFor="language">{t("settings.language")}</Label>
        <Select value={g.language} onValueChange={(v) => update((d) => void (d.gen.language = v))}>
          <SelectTrigger id="language" className="w-64" data-sf="language">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {langs.map((code) => (
              <SelectItem key={code} value={code} data-language={code}>
                {t(`settings.languages.${code}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="flex items-center gap-3">
        <Switch id="notify-enabled" checked={g.enabled} onCheckedChange={(v) => update((d) => void (d.gen.enabled = v))} data-sf="notify-enabled" />
        <Label htmlFor="notify-enabled" className="font-normal">
          {t("settings.notifyEnabled")}
        </Label>
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="notify-server">{t("settings.notifyServer")}</Label>
        <Input id="notify-server" value={g.server} onChange={(ev) => update((d) => void (d.gen.server = ev.target.value))} data-sf="notify-server" />
      </div>
      <div className="grid max-w-48 gap-1.5">
        <Label htmlFor="notify-timeout">{t("settings.decisionTimeout")}</Label>
        <Input
          id="notify-timeout"
          type="number"
          min={0}
          step="any"
          value={g.timeout}
          onChange={(ev) => update((d) => void (d.gen.timeout = ev.target.value))}
          data-sf="notify-timeout"
        />
      </div>
      {(draft.cfg.warnings || []).map((w) => (
        <Alert key={w} variant="warning" data-warning={w}>
          <TriangleAlertIcon />
          <AlertDescription className="text-foreground">{t(`settings.warnings.${w}`)}</AlertDescription>
        </Alert>
      ))}
      <p className="text-xs text-muted-foreground">{t("settings.topicHint")}</p>
    </div>
  );
}
