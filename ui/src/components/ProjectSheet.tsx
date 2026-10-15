import { useState, type ReactNode } from "react";
import { InfoTip } from "@/components/InfoTip";
import { toast } from "sonner";
import { enc, send, type Decision, type Overview, type Settings, type SetupDetail } from "@/api";
import { ActionButton } from "@/components/ActionButton";
import { useEngineName } from "@/components/Engines";
import { History } from "@/components/History";
import { StatusBadge, projectState } from "@/components/Projects";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useProjectDetail } from "@/hooks/useProjectDetail";
import { useI18n } from "@/i18n";
import { parseCap, retryAt, when } from "@/lib/format";
import { cn } from "@/lib/utils";

function Section({ title, children, ...rest }: { title: string; children: ReactNode; "data-section"?: string }) {
  return (
    <section className="grid gap-2" {...rest}>
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  );
}

function RetryRow({ name, overview }: { name: string; overview: Overview }) {
  const { t, lang } = useI18n();
  const p = overview.projects.find((x) => x.name === name);
  const times = p ? Object.values(p.engines).map(retryAt).filter((x): x is number => !!x) : [];
  const [hidden, setHidden] = useState(false);
  if (!times.length || hidden) return null;
  return (
    <Alert variant="warning" data-section="retry">
      <AlertDescription className="flex flex-wrap items-center gap-2 text-foreground">
        <span>{t("retry", { when: when(lang, Math.min(...times)) })}</span>
        <ActionButton
          size="sm"
          variant="outline"
          data-action={`retry:${name}`}
          action={() => send(`/api/projects/${enc(name)}/reset`, {})}
          ok={t("project.retryRequested", { project: name })}
          onDone={(ok) => ok && setHidden(true)}
        >
          {t("project.retryNow")}
        </ActionButton>
      </AlertDescription>
    </Alert>
  );
}

function SetupPanel({ name, su, reload }: { name: string; su: SetupDetail | null; reload: () => void }) {
  const { t } = useI18n();
  const [changes, setChanges] = useState("");
  const r = su?.request;
  if (!r || r.status === "done") return null;
  const files = su?.files ?? [];
  return (
    <Section title={`${t("setup.title")} · ${name}`} data-section="setup">
      <p className="text-sm">
        <b>{`${t("setup.goal")}: `}</b>
        <span className="whitespace-pre-wrap">{r.goal}</span>
      </p>
      <Badge variant="secondary" data-setup-phase={r.status}>{t(`setup.phase.${r.status}`)}</Badge>
      {r.feedback ? (
        <p className="text-xs text-muted-foreground">
          <b>{`${t("setup.feedback")}: `}</b>
          {r.feedback}
        </p>
      ) : null}
      {r.status === "review" ? (
        <>
          <h4 className="text-sm font-medium">{t("setup.files")}</h4>
          {files.length ? (
            files.map((f) => (
              <article key={f.path} className="grid gap-1" data-file={f.path}>
                <h5 className="font-mono text-xs">{f.path}</h5>
                {f.text == null ? (
                  <p className="text-xs text-muted-foreground">{t("setup.tooLarge", { size: f.size })}</p>
                ) : (
                  <ScrollArea className="max-h-64 rounded-md border bg-muted/40">
                    <pre className="p-3 font-mono text-xs whitespace-pre-wrap">{f.text}</pre>
                  </ScrollArea>
                )}
              </article>
            ))
          ) : (
            <p className="text-xs text-muted-foreground">{t("setup.noFiles")}</p>
          )}
          <Textarea
            rows={3}
            maxLength={4000}
            value={changes}
            onChange={(ev) => setChanges(ev.target.value)}
            aria-label={t("setup.changesLabel")}
            placeholder={t("setup.changesLabel")}
            data-action="setup-changes-text"
          />
          <div className="flex flex-wrap gap-2">
            <ActionButton
              size="sm"
              data-action="setup-approve"
              action={() => send(`/api/projects/${enc(name)}/setup/approve`, {})}
              ok={t("setup.approved")}
              onDone={(ok) => ok && reload()}
            >
              {t("setup.approve")}
            </ActionButton>
            <ActionButton
              size="sm"
              variant="outline"
              data-action="setup-changes"
              disabled={!changes.trim()}
              action={() => send(`/api/projects/${enc(name)}/setup/changes`, { feedback: changes.trim() })}
              ok={t("setup.changesSent")}
              onDone={(ok) => {
                if (ok) {
                  setChanges("");
                  reload();
                }
              }}
            >
              {t("setup.changes")}
            </ActionButton>
          </div>
        </>
      ) : null}
      {r.status !== "approved" ? (
        <div>
          <ActionButton
            size="sm"
            variant="destructive"
            data-action="setup-cancel"
            action={() => send(`/api/projects/${enc(name)}/setup`, undefined, "DELETE")}
            ok={t("setup.cancelled")}
            onDone={(ok) => ok && reload()}
          >
            {t("setup.cancel")}
          </ActionButton>
        </div>
      ) : null}
    </Section>
  );
}

function MessageForm({ name }: { name: string }) {
  const { t } = useI18n();
  const [text, setText] = useState("");
  return (
    <Section title={t("message.title")} data-section="message">
      <Textarea
        rows={3}
        maxLength={2000}
        value={text}
        onChange={(ev) => setText(ev.target.value)}
        aria-label={t("message.title")}
        placeholder={t("message.placeholder")}
      />
      <div>
        <ActionButton
          size="sm"
          data-action="message-send"
          action={() => send("/api/messages", { text, projects: [name] })}
          ok={t("message.sent")}
          disabled={!text.trim()}
          onDone={(ok) => ok && setText("")}
        >
          {t("message.send")}
        </ActionButton>
      </div>
    </Section>
  );
}

function CapsForm({ name, settings }: { name: string; settings: Settings }) {
  const { t } = useI18n();
  const engineName = useEngineName();
  const engs = Object.keys(settings.engines);
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(engs.map((e) => [e, settings.engines[e].weekly_cap[name] == null ? "" : String(settings.engines[e].weekly_cap[name])])),
  );
  return (
    <Section title={t("caps.title")} data-section="caps">
      <div className="grid grid-cols-2 gap-3">
        {engs.map((e) => (
          <div key={e} className="grid gap-1.5">
            <div className="flex items-center gap-1.5">
              <Label htmlFor={`cap-${name}-${e}`}>{`${engineName(e)} (%)`}</Label>
              <InfoTip text={t("help.weeklyCap")} />
            </div>
            <Input
              id={`cap-${name}-${e}`}
              type="number"
              min={0}
              max={100}
              step="any"
              value={values[e] ?? ""}
              onChange={(ev) => setValues((v) => ({ ...v, [e]: ev.target.value }))}
            />
          </div>
        ))}
      </div>
      <div>
        <ActionButton
          size="sm"
          variant="outline"
          data-action="caps-save"
          action={async () => {
            const body: Record<string, number> = {};
            for (const e of engs) {
              if (String(values[e] ?? "").trim() === "") continue; // empty: leave that value alone
              const n = parseCap(values[e]);
              if (n == null) {
                toast.error(t("caps.invalid"));
                return;
              }
              if (n !== settings.engines[e].weekly_cap[name]) body[e] = n;
            }
            if (!Object.keys(body).length) {
              toast(t("caps.nothing"));
              return;
            }
            await send("/api/settings", { weekly_cap: { [name]: body } }, "PUT");
            toast.success(t("caps.saved"));
          }}
        >
          {t("caps.save")}
        </ActionButton>
      </div>
    </Section>
  );
}

function PendingDecisions({ decisions, overview }: { decisions: Decision[]; overview: Overview }) {
  const { t, lang } = useI18n();
  if (!decisions.length) return null;
  const where = t(overview.notify.enabled && overview.notify.has_topic ? "project.answerByPhone" : "project.answerInPanel");
  return (
    <Section title={t("project.decisions")} data-section="decisions">
      {decisions.map((x) => (
        <article key={x.id} className="grid gap-1 rounded-lg border p-3 text-sm">
          <p className="font-medium">{x.question}</p>
          {x.context ? <p className="text-xs whitespace-pre-wrap text-muted-foreground">{x.context}</p> : null}
          <ul className="grid gap-0.5">
            {x.options.map((o) => (
              <li key={o.id}>
                {`${o.id.toUpperCase()}. ${o.label}`}
                {o.id === x.default ? ` (${t("project.default")})` : ""}
              </li>
            ))}
          </ul>
          <p className="text-xs text-muted-foreground">
            {x.deadline ? `${t("project.deadline", { when: when(lang, x.deadline) })}. ` : ""}
            {where}
          </p>
        </article>
      ))}
    </Section>
  );
}

export function ProjectSheet({ name, overview, tick, onClose }: { name: string | null; overview: Overview; tick: number; onClose: () => void }) {
  const { t } = useI18n();
  const p = name ? overview.projects.find((x) => x.name === name) : undefined;
  const { detail, settings, setup, error, reload } = useProjectDetail(name, p?.setup ?? null);
  return (
    <Sheet open={!!name} onOpenChange={(o) => !o && onClose()}>
      <SheetContent closeLabel={t("app.close")} data-sheet={name ?? ""}>
        <SheetHeader>
          <SheetTitle className="flex flex-wrap items-center gap-2">
            {name}
            {p ? <StatusBadge s={projectState(p)} /> : null}
          </SheetTitle>
          <SheetDescription className="sr-only">{t("projects.open", { project: name })}</SheetDescription>
        </SheetHeader>
        {name ? (
          <div className="grid gap-5 px-4 pb-6">
            <RetryRow key={`retry:${name}`} name={name} overview={overview} />
            {error ? <p className="text-sm text-destructive">{t("loadError")}</p> : null}
            {!detail && !error ? (
              <div className="grid gap-2">
                <Skeleton className="h-6 w-1/2" />
                <Skeleton className="h-20 w-full" />
              </div>
            ) : null}
            {detail ? (
              <>
                <SetupPanel key={`setup:${name}`} name={name} su={setup} reload={reload} />
                <Section title={`${t("project.backlog")} · ${name}`} data-section="backlog">
                  {"error" in detail.backlog ? (
                    <p className="text-sm text-muted-foreground">{t(`backlog.${detail.backlog.error}`)}</p>
                  ) : (
                    <ol className="grid gap-1.5">
                      {detail.backlog.items.map((i, k) => (
                        <li key={k} className={cn("flex items-center justify-between gap-2 rounded-md border px-3 py-2 text-sm", i.state === "done" && "text-muted-foreground")}>
                          <span className={cn(i.state === "done" && "line-through")}>{i.title}</span>
                          <Badge variant={i.state === "doing" ? "success" : i.state === "done" ? "outline" : "secondary"}>
                            {i.tasks ? `${i.tasks.done}/${i.tasks.total}` : t(`backlog.state.${i.state}`)}
                          </Badge>
                        </li>
                      ))}
                    </ol>
                  )}
                </Section>
                <PendingDecisions decisions={detail.decisions} overview={overview} />
                <Separator />
                <MessageForm key={`msg:${name}`} name={name} />
                {settings ? <CapsForm key={`caps:${name}`} name={name} settings={settings} /> : null}
              </>
            ) : null}
            <Separator />
            <Section title={t("history.title")} data-section="project-history-title">
              <History tick={tick} project={name} compact />
            </Section>
          </div>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
