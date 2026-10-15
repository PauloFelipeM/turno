import type { ReactNode } from "react";
import { FolderPlusIcon, PauseIcon, PlayIcon, SkipForwardIcon, SquareIcon } from "lucide-react";
import { enc, send, type Overview, type ProjectEngineState, type ProjectView } from "@/api";
import { ActionButton } from "@/components/ActionButton";
import { Confirm } from "@/components/Confirm";
import { useEngineName } from "@/components/Engines";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useI18n } from "@/i18n";
import { retryAt, when } from "@/lib/format";
import { cn } from "@/lib/utils";

/** The state shown on the card: the claiming engine's, else the first engine's. */
export function projectState(p: ProjectView): Partial<ProjectEngineState> {
  const c = p.claim;
  const name = c && c.engine !== "any" && p.engines[c.engine] ? c.engine : Object.keys(p.engines)[0];
  return p.engines[name] || {};
}

const STATUS_BADGE: Record<string, BadgeProps["variant"]> = {
  active: "success",
  waiting: "warning",
  blocked: "destructive",
  idle: "secondary",
  interrupted: "warning",
};

export function StatusBadge({ s }: { s: Partial<ProjectEngineState> }) {
  const { t, lang } = useI18n();
  const key = s.interrupted ? "interrupted" : s.status || "active";
  return (
    <Badge variant={STATUS_BADGE[key] ?? "secondary"} data-status={key}>
      {t(`status.${key}`, { until: when(lang, s.blocked_until) })}
    </Badge>
  );
}

function IconAction({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

function Backlog({ b }: { b: ProjectView["backlog"] }) {
  const { t } = useI18n();
  if ("error" in b) return <p className="text-xs text-muted-foreground">{t(`backlog.${b.error}`)}</p>;
  const cur = b.current;
  const tasks = cur?.tasks;
  return (
    <div className="grid gap-1.5">
      {cur ? (
        <p className="truncate text-sm" title={cur.title}>
          {cur.title} · <span className="text-muted-foreground">{tasks ? `${tasks.done}/${tasks.total}` : t(`backlog.state.${cur.state}`)}</span>
        </p>
      ) : null}
      {tasks ? <Progress value={tasks.total ? (100 * tasks.done) / tasks.total : 0} aria-label={cur?.title} /> : null}
      <p className="text-xs text-muted-foreground">
        {t("backlog.items", { done: b.done, total: b.total })}
        {b.stale ? ` · ${t("backlog.stale")}` : ""}
      </p>
    </div>
  );
}

function ProjectCard({ p, running, onOpen }: { p: ProjectView; running: boolean; onOpen: () => void }) {
  const { t, lang } = useI18n();
  const engineName = useEngineName();
  const s = projectState(p);
  const claim = p.claim
    ? p.claim.engine === "any"
      ? t("baton", { engine: engineName(p.claim.handoff_from) })
      : t("claim", { engine: engineName(p.claim.engine) })
    : null;
  const extra = [claim, p.decisions ? (p.decisions === 1 ? t("decisionsOne") : t("decisionsMany", { n: p.decisions })) : null]
    .filter(Boolean)
    .join(" · ");
  const retry = retryAt(s);
  const label = (k: string) => t(`card.${k}`, { project: p.name });
  const toggle = p.paused ? "resume" : "pause";
  return (
    <Card
      data-project-card={p.name}
      className={cn("gap-3 py-4 transition-shadow hover:shadow-md", running && "ring-2 ring-success/60", p.paused && "opacity-75")}
    >
      <button
        type="button"
        data-project={p.name}
        onClick={onOpen}
        aria-label={t("projects.open", { project: p.name })}
        className="grid gap-2 rounded-md px-4 text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
      >
        <span className="flex flex-wrap items-center gap-1.5">
          <b className="mr-auto truncate">{p.name}</b>
          {p.paused ? <Badge variant="outline">{t("card.pausedChip")}</Badge> : null}
          {p.next ? <Badge variant="secondary">{t("card.nextBadge")}</Badge> : null}
          {p.setup && p.setup !== "done" ? <Badge variant="secondary" data-setup-chip={p.setup}>{t(`setup.chip.${p.setup}`)}</Badge> : null}
          <StatusBadge s={s} />
        </span>
        {extra ? <span className="text-xs text-muted-foreground">{extra}</span> : null}
        {retry ? <span className="text-xs text-muted-foreground">{t("retry", { when: when(lang, retry) })}</span> : null}
        <Backlog b={p.backlog} />
      </button>
      <CardFooter className="justify-end gap-1 px-3">
        <IconAction label={label(toggle)}>
          <ActionButton
            size="icon-sm"
            variant="ghost"
            aria-label={label(toggle)}
            data-action={`${toggle}:${p.name}`}
            action={() => send(`/api/projects/${enc(p.name)}/${toggle}`)}
            ok={t(`card.${p.paused ? "resumed" : "paused"}`, { project: p.name })}
          >
            {p.paused ? <PlayIcon /> : <PauseIcon />}
          </ActionButton>
        </IconAction>
        {p.next ? null : (
          <IconAction label={label("next")}>
            <ActionButton
              size="icon-sm"
              variant="ghost"
              aria-label={label("next")}
              data-action={`next:${p.name}`}
              action={() => send(`/api/projects/${enc(p.name)}/next`)}
              ok={t("card.nextSet", { project: p.name })}
            >
              <SkipForwardIcon />
            </ActionButton>
          </IconAction>
        )}
        {running ? (
          <Confirm
            data-action={`interrupt-confirm:${p.name}`}
            tooltip={label("interrupt")}
            title={label("interrupt")}
            description={t("card.interruptConfirm", { project: p.name })}
            confirm={t("card.interrupt", { project: p.name })}
            action={() => send(`/api/projects/${enc(p.name)}/interrupt`)}
            ok={t("card.interrupted", { project: p.name })}
            trigger={
              <Button size="icon-sm" variant="ghost" aria-label={label("interrupt")} data-action={`interrupt:${p.name}`}>
                <SquareIcon />
              </Button>
            }
          />
        ) : null}
      </CardFooter>
    </Card>
  );
}

export function Projects({ overview, onOpen, onAdd }: { overview: Overview; onOpen: (name: string) => void; onAdd: () => void }) {
  const { t } = useI18n();
  const running = new Set(overview.live.map((l) => l.project));
  return (
    <section aria-labelledby="projects-h" className="grid gap-3">
      <h2 id="projects-h" className="text-sm font-semibold tracking-wide text-muted-foreground uppercase">
        {t("projects.title")}
      </h2>
      {overview.projects.length ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {overview.projects.map((p) => (
            <ProjectCard key={p.name} p={p} running={running.has(p.name)} onOpen={() => onOpen(p.name)} />
          ))}
        </div>
      ) : (
        <Card className="items-center gap-3 border-dashed py-10 text-center" data-empty="projects">
          <FolderPlusIcon className="size-8 text-muted-foreground" />
          <CardContent className="grid gap-1">
            <p className="font-medium">{t("projects.empty")}</p>
            <p className="text-sm text-muted-foreground">{t("projects.emptyHint")}</p>
          </CardContent>
          <Button onClick={onAdd} data-action="add-project">
            {t("projects.add")}
          </Button>
        </Card>
      )}
    </section>
  );
}
