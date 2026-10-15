import { useState, type ReactNode } from "react";
import { InfoTip } from "@/components/InfoTip";
import { enc, send, type CheckResult, type Limits } from "@/api";
import { useEngineName } from "@/components/Engines";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useI18n } from "@/i18n";
import { pct, when } from "@/lib/format";
import { EFFORTS, ROLES, type Draft, type EngineDraft } from "./draft";
import { ModelCombobox } from "./ModelCombobox";
import { WindowPointPicker } from "./WindowPicker";

function SwitchRow({ id, checked, onChange, children }: { id: string; checked: boolean; onChange: (v: boolean) => void; children: ReactNode }) {
  return (
    <div className="flex items-center gap-3">
      <Switch id={id} checked={checked} onCheckedChange={onChange} data-sf={id} />
      <Label htmlFor={id} className="leading-snug font-normal">
        {children}
      </Label>
    </div>
  );
}

function EffortSelect({ value, onChange, label, sf }: { value: string; onChange: (v: string) => void; label: string; sf: string }) {
  const efforts = EFFORTS.includes(value) ? EFFORTS : [...EFFORTS, value];
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="w-28" aria-label={label} data-sf={sf}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {efforts.map((x) => (
          <SelectItem key={x} value={x} data-effort={x}>
            {x}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function LimitText({ l }: { l: Limits }) {
  const { t, lang } = useI18n();
  const win = (label: string, w: Limits["primary"]) =>
    `${label} ${w ? pct(lang, w.used_percent) : "-"}${w && w.resets_at ? ` (${t("reset", { when: when(lang, w.resets_at) })})` : ""}`;
  return <>{`${win(t("window5h"), l.primary)} · ${win(t("weekly"), l.secondary)}`}</>;
}

/** The quota check outcome: known errors get a text of their own; others show the original as a technical detail. */
function CheckOutcome({ k, res }: { k: string; res: CheckResult }) {
  const { t, has } = useI18n();
  if (res.limits)
    return (
      <p className="text-sm" data-check={k}>
        <LimitText l={res.limits} />
      </p>
    );
  const err = res.error || "";
  const known = !!err && has(`settings.checkErrors.${err}`);
  return (
    <p className="text-sm text-destructive" data-check={k}>
      {known ? t(`settings.checkErrors.${err}`) : t("settings.checkErrorOther")}
      {known || !err ? null : <span className="ml-1 font-mono text-xs text-muted-foreground">{err}</span>}
    </p>
  );
}

function EngineBlock({ k, d, set }: { k: string; d: Draft; set: (fn: (e: EngineDraft) => void) => void }) {
  const { t, errorText } = useI18n();
  const engineName = useEngineName();
  const cur = d.eng[k];
  const cfg = d.cfg.engines[k];
  const cli = cfg.cli || { found: false };
  const known = cfg.known_models || [];
  const [check, setCheck] = useState<CheckResult | "loading" | null>(null);
  const roleRow = (label: string, model: string, effort: string, setModel: (v: string) => void, setEffort: (v: string) => void, key: string) => (
    <TableRow key={key || "orchestrator"} data-role={key || "orchestrator"}>
      <TableHead scope="row" className="font-medium text-foreground">
        {label}
      </TableHead>
      <TableCell>
        <ModelCombobox value={model} onChange={setModel} known={known} label={`${label}: ${t("settings.model")}`} sf={`model:${k}${key}`} />
      </TableCell>
      <TableCell>
        <EffortSelect value={effort} onChange={setEffort} label={`${label}: ${t("settings.effort")}`} sf={`effort:${k}${key}`} />
      </TableCell>
    </TableRow>
  );
  return (
    <Card className="gap-4" data-settings-engine={k}>
      <CardHeader className="items-center">
        <CardTitle>{engineName(k)}</CardTitle>
        <Badge
          variant={cli.found ? "secondary" : "destructive"}
          className="ml-auto block min-w-0 max-w-full truncate font-normal"
          title={cli.found ? t("settings.cliFound", { path: cli.path, version: cli.version || "?" }) : undefined}
          data-cli={cli.found ? "found" : "missing"}
        >
          {cli.found ? t("settings.cliFound", { path: cli.path, version: cli.version || "?" }) : t("settings.cliMissing")}
        </Badge>
      </CardHeader>
      <CardContent className="grid gap-4">
        <SwitchRow id={`enabled:${k}`} checked={cur.enabled} onChange={(v) => set((e) => void (e.enabled = v))}>
          {t("settings.enabled")}
        </SwitchRow>
        <SwitchRow id={`autostart:${k}`} checked={cur.autostart} onChange={(v) => set((e) => void (e.autostart = v))}>
          {t("settings.autostart")}
        </SwitchRow>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("settings.role")}</TableHead>
              <TableHead>{t("settings.model")}</TableHead>
              <TableHead>{t("settings.effort")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {roleRow(
              t("settings.roles.orchestrator"),
              cur.orchestrator_model,
              cur.orchestrator_effort,
              (v) => set((e) => void (e.orchestrator_model = v)),
              (v) => set((e) => void (e.orchestrator_effort = v)),
              "",
            )}
            {ROLES.map((r) =>
              roleRow(
                t(`settings.roles.${r}`),
                cur.roles[r].model,
                cur.roles[r].effort,
                (v) => set((e) => void (e.roles[r].model = v)),
                (v) => set((e) => void (e.roles[r].effort = v)),
                `:${r}`,
              ),
            )}
          </TableBody>
        </Table>
        <div className="grid max-w-48 gap-1.5">
          <div className="flex items-center gap-1.5">
            <Label htmlFor={`primary:${k}`}>{t("settings.primaryCap")}</Label>
            <InfoTip text={t("help.primaryCap")} />
          </div>
          <Input
            id={`primary:${k}`}
            type="number"
            min={0}
            max={100}
            step="any"
            value={cur.primary_cap}
            onChange={(ev) => set((e) => void (e.primary_cap = ev.target.value))}
            data-sf={`primary:${k}`}
          />
        </div>
        <div className="grid gap-3 rounded-lg border p-3">
          <SwitchRow id={`nowindow:${k}`} checked={cur.noWindow} onChange={(v) => set((e) => void (e.noWindow = v))}>
            {t("settings.noWindow")}
          </SwitchRow>
          {cur.noWindow ? null : (
            <div className="flex flex-wrap items-end gap-4">
              <WindowPointPicker id={`start:${k}`} label={t("settings.start")} value={cur.start} onChange={(v) => set((e) => void (e.start = v))} />
              <WindowPointPicker id={`end:${k}`} label={t("settings.end")} value={cur.end} onChange={(v) => set((e) => void (e.end = v))} />
              <div className="grid gap-1.5">
                <Label htmlFor={`margin:${k}`}>{t("settings.margin")}</Label>
                <div className="flex items-center gap-2">
                  <Input
                    id={`margin:${k}`}
                    type="number"
                    min={0}
                    step={1}
                    className="w-24"
                    value={cur.margin}
                    onChange={(ev) => set((e) => void (e.margin = ev.target.value))}
                    data-sf={`margin:${k}`}
                  />
                  <span className="text-sm text-muted-foreground">{t("settings.minutes")}</span>
                </div>
              </div>
            </div>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="outline"
            size="sm"
            data-sf={`check:${k}`}
            disabled={check === "loading"}
            onClick={async () => {
              setCheck("loading");
              try {
                setCheck(await send<CheckResult>(`/api/engines/${enc(k)}/check`));
              } catch (e) {
                setCheck({ limits: null, error: errorText(e) });
              }
            }}
          >
            {check === "loading" ? t("settings.checking") : t("settings.check")}
          </Button>
          {check && check !== "loading" ? <CheckOutcome k={k} res={check} /> : null}
        </div>
      </CardContent>
    </Card>
  );
}

export function EnginesTab({ draft, update }: { draft: Draft; update: (fn: (d: Draft) => void) => void }) {
  return (
    <div className="grid gap-4">
      {Object.keys(draft.eng).map((k) => (
        <EngineBlock key={k} k={k} d={draft} set={(fn) => update((d) => fn(d.eng[k]))} />
      ))}
    </div>
  );
}
