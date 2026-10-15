import { PlayIcon, SquareIcon } from "lucide-react";
import { enc, send, type EngineView, type Overview, type Usage } from "@/api";
import { ActionButton } from "@/components/ActionButton";
import { Confirm } from "@/components/Confirm";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { useI18n } from "@/i18n";
import { pct, when } from "@/lib/format";

const PHASE_BADGE: Record<string, BadgeProps["variant"]> = {
  running: "success",
  sleeping: "secondary",
  outside_window: "secondary",
  disabled: "outline",
  stopped: "outline",
  unknown: "success",
};

export function useEngineName() {
  const { t, has } = useI18n();
  return (e: string | null | undefined) => (e && has(`engine.${e}`) ? t(`engine.${e}`) : (e ?? ""));
}

export function phaseText(t: ReturnType<typeof useI18n>["t"], lang: string, e: EngineView) {
  const p = e.loop || { phase: "unknown" };
  return t(`phase.${p.phase || "unknown"}`, { project: p.project ?? "", until: when(lang, p.until) });
}

function Meter({ label, usage, cap }: { label: string; usage: Usage; cap: number | null }) {
  const { t, lang } = useI18n();
  const used = Math.min(100, usage.used || 0);
  const hot = cap != null && cap > 0 && used >= cap;
  return (
    <div className="grid gap-1.5">
      <div className="flex items-baseline justify-between gap-2 text-sm">
        <span className="font-medium">{label}</span>
        <span className="tabular">{pct(lang, usage.used)}</span>
      </div>
      <Progress
        value={used}
        aria-label={`${label}: ${pct(lang, usage.used)}`}
        marker={cap ? Math.min(cap, 100) : null}
        markerLabel={cap ? t("cap", { cap }) : undefined}
        indicatorClassName={hot ? "bg-destructive" : undefined}
      />
      <div className="flex justify-between gap-2 text-xs text-muted-foreground">
        <span>{cap ? t("cap", { cap }) : ""}</span>
        <span>{usage.resets_at ? t("reset", { when: when(lang, usage.resets_at) }) : ""}</span>
      </div>
    </div>
  );
}

function Controls({ e }: { e: EngineView }) {
  const { t } = useI18n();
  const engineName = useEngineName();
  const name = engineName(e.engine);
  const running = e.loop.phase !== "stopped";
  if (!running && !e.enabled) return <span className="text-sm text-muted-foreground">{t("disabled")}</span>;
  if (!running) {
    return (
      <ActionButton
        size="sm"
        data-action={`start:${e.engine}`}
        action={() => send(`/api/engines/${enc(e.engine)}/start`)}
        ok={t("engineStarting", { engine: name })}
      >
        <PlayIcon />
        {t("start")}
      </ActionButton>
    );
  }
  const unit = e.loop.phase === "running" && e.loop.project ? t("stopConfirmUnit", { project: e.loop.project }) : "";
  return (
    <Confirm
      data-action={`stop-confirm:${e.engine}`}
      title={t("stop")}
      description={t("stopConfirm", { engine: name, unit })}
      confirm={t("stop")}
      action={() => send(`/api/engines/${enc(e.engine)}/stop`)}
      ok={t("engineStopping", { engine: name })}
      trigger={
        <Button size="sm" variant="outline" data-action={`stop:${e.engine}`}>
          <SquareIcon />
          {t("stop")}
        </Button>
      }
    />
  );
}

function EngineCard({ e }: { e: EngineView }) {
  const { t, lang } = useI18n();
  return (
    <Card className="gap-3" data-engine={e.engine}>
      <CardHeader className="items-center justify-between">
        <CardTitle className="text-base">{e.label}</CardTitle>
        <Badge variant={PHASE_BADGE[e.loop.phase] ?? "secondary"} data-phase={e.loop.phase}>
          {e.loop.phase === "running" || e.loop.phase === "unknown" ? <span className="size-1.5 animate-pulse rounded-full bg-current" /> : null}
          {phaseText(t, lang, e)}
        </Badge>
      </CardHeader>
      <CardContent className="grid gap-3">
        {e.limits.known ? (
          <>
            <Meter label={t("window5h")} usage={e.limits.primary} cap={e.primary_cap} />
            <Meter label={t("weekly")} usage={e.limits.secondary} cap={e.weekly_cap} />
          </>
        ) : (
          <p className="text-sm text-muted-foreground">{t("noLimits")}</p>
        )}
        {e.window ? <p className="text-xs text-muted-foreground">{e.window.label}</p> : null}
      </CardContent>
      <CardFooter>
        <Controls e={e} />
      </CardFooter>
    </Card>
  );
}

export function Engines({ overview }: { overview: Overview }) {
  const { t } = useI18n();
  return (
    <section aria-label={t("app.engines")} className="grid gap-3">
      <div className="grid gap-4 sm:grid-cols-2">
        {overview.engines.map((e) => (
          <EngineCard key={e.engine} e={e} />
        ))}
      </div>
      {overview.errors.map((err) => (
        <Alert key={err.config} variant="destructive">
          <AlertDescription>{t("configError", { config: err.config })}</AlertDescription>
        </Alert>
      ))}
    </section>
  );
}
