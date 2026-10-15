import type { ReactNode } from "react";
import { InfoTip } from "@/components/InfoTip";
import { ArrowDownIcon, ArrowUpIcon, TriangleAlertIcon } from "lucide-react";
import { toast } from "sonner";
import { enc, send, type ProjectConfig } from "@/api";
import { ActionButton } from "@/components/ActionButton";
import { Confirm } from "@/components/Confirm";
import { useEngineName } from "@/components/Engines";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/i18n";
import { readCaps, type AddDraft, type Draft } from "./draft";

export interface SetupBoxState {
  open: string | null;
  goal: string;
}

interface Props {
  draft: Draft;
  update: (fn: (d: Draft) => void) => void;
  add: AddDraft;
  setAdd: (a: AddDraft) => void;
  setup: SetupBoxState;
  setSetup: (s: SetupBoxState) => void;
  /** Reloads GET /api/config into the draft, keeping unsaved edits. */
  refresh: () => Promise<void>;
  /** After a project is added: reload and, for a project without docs/autopilot.md, open its setup box. */
  onAdded: (name: string) => Promise<void>;
}

function Field({ id, label, help, children }: { id: string; label: string; help?: string; children: ReactNode }) {
  return (
    <div className="grid gap-1.5">
      <div className="flex items-center gap-1.5">
        <Label htmlFor={id}>{label}</Label>
        {help ? <InfoTip text={help} /> : null}
      </div>
      {children}
    </div>
  );
}

/** "Preparar com um agente": for a git project with no docs/autopilot.md and no open request. */
function SetupBox({ p, setup, setSetup, refresh }: { p: ProjectConfig; setup: SetupBoxState; setSetup: (s: SetupBoxState) => void; refresh: () => Promise<void> }) {
  const { t } = useI18n();
  if (p.setup && p.setup !== "done") return <p className="text-xs text-muted-foreground">{t("setup.pending", { phase: t(`setup.phase.${p.setup}`) })}</p>;
  if (!p.checks.includes("no_autopilot_doc") || p.checks.includes("not_git") || p.checks.includes("missing_path")) return null;
  if (setup.open !== p.name) {
    return (
      <div>
        <Button size="sm" variant="outline" data-sf={`setup-open:${p.name}`} onClick={() => setSetup({ open: p.name, goal: "" })}>
          {t("setup.prepare")}
        </Button>
      </div>
    );
  }
  return (
    <div className="grid gap-2 rounded-md border bg-muted/30 p-3">
      <Label htmlFor={`setup-goal-${p.name}`}>{t("setup.goalLabel")}</Label>
      <Textarea
        id={`setup-goal-${p.name}`}
        rows={3}
        maxLength={4000}
        placeholder={t("setup.goalPlaceholder")}
        value={setup.goal}
        onChange={(ev) => setSetup({ open: p.name, goal: ev.target.value })}
        data-sf={`setup-goal:${p.name}`}
      />
      <div className="flex gap-2">
        <ActionButton
          size="sm"
          data-sf={`setup-go:${p.name}`}
          disabled={!setup.goal.trim()}
          action={async () => {
            await send(`/api/projects/${enc(p.name)}/setup`, { goal: setup.goal.trim() });
            setSetup({ open: null, goal: "" });
            await refresh();
          }}
          ok={t("setup.requested", { project: p.name })}
        >
          {t("setup.request")}
        </ActionButton>
        <Button size="sm" variant="ghost" onClick={() => setSetup({ open: null, goal: "" })}>
          {t("cancel")}
        </Button>
      </div>
    </div>
  );
}

function ProjectRow({ n, i, props }: { n: string; i: number; props: Props }) {
  const { t } = useI18n();
  const engineName = useEngineName();
  const { draft: d, update } = props;
  const p = d.cfg.projects.find((x) => x.name === n);
  const cur = d.proj[n];
  const why = p && !p.removable && p.blocked_by ? t(`settings.blocked.${p.blocked_by}`) : "";
  const move = (delta: number) =>
    update((x) => {
      const o = x.order;
      [o[i], o[i + delta]] = [o[i + delta], o[i]];
    });
  return (
    <li className="grid gap-3 rounded-lg border p-3" data-settings-project={n}>
      <div className="flex items-center gap-2">
        <span className="w-6 text-center text-xs text-muted-foreground tabular">{i + 1}</span>
        <b className="mr-auto truncate">{n}</b>
        <Button size="icon-sm" variant="ghost" disabled={i === 0} aria-label={t("settings.up", { project: n })} title={t("settings.up", { project: n })} data-mv={`up:${n}`} onClick={() => move(-1)}>
          <ArrowUpIcon />
        </Button>
        <Button
          size="icon-sm"
          variant="ghost"
          disabled={i === d.order.length - 1}
          aria-label={t("settings.down", { project: n })}
          title={t("settings.down", { project: n })}
          data-mv={`down:${n}`}
          onClick={() => move(1)}
        >
          <ArrowDownIcon />
        </Button>
      </div>
      <Field id={`path-${n}`} label={t("settings.path")}>
        <Input id={`path-${n}`} value={cur.path} onChange={(ev) => update((x) => void (x.proj[n].path = ev.target.value))} data-sf={`path:${n}`} className="font-mono text-xs" />
      </Field>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Field id={`group-${n}`} label={t("settings.group")} help={t("help.group")}>
          <Input id={`group-${n}`} value={cur.group} onChange={(ev) => update((x) => void (x.proj[n].group = ev.target.value))} data-sf={`group:${n}`} />
        </Field>
        {Object.keys(cur.caps).map((e) => (
          <Field key={e} id={`cap-${n}-${e}`} label={`${engineName(e)} (%)`} help={t("help.weeklyCap")}>
            <Input
              id={`cap-${n}-${e}`}
              type="number"
              min={0}
              max={100}
              step="any"
              value={cur.caps[e]}
              onChange={(ev) => update((x) => void (x.proj[n].caps[e] = ev.target.value))}
              data-sf={`cap:${n}:${e}`}
            />
          </Field>
        ))}
      </div>
      {p && p.checks.length ? (
        <ul className="grid gap-1">
          {p.checks.map((c) => (
            <li key={c} className="flex items-center gap-1.5 text-xs text-[color:var(--warning)]" data-check-code={c}>
              <TriangleAlertIcon className="size-3.5" />
              {t(`settings.checks.${c}`)}
            </li>
          ))}
        </ul>
      ) : null}
      {p ? <SetupBox p={p} setup={props.setup} setSetup={props.setSetup} refresh={props.refresh} /> : null}
      <div className="flex flex-wrap items-center gap-2">
        <Confirm
          data-action={`remove-yes:${n}`}
          title={t("settings.remove")}
          description={t("settings.removeConfirm", { project: n })}
          confirm={t("settings.remove")}
          action={async () => {
            try {
              await send(`/api/projects/${enc(n)}`, undefined, "DELETE");
            } finally {
              // The list is refreshed either way; a failing refresh must not hide the error of the removal.
              await props.refresh().catch(() => undefined);
            }
          }}
          ok={t("settings.removed", { project: n })}
          trigger={
            <Button size="sm" variant="outline" className="text-destructive" disabled={!!why} title={why || undefined} data-sf={`remove:${n}`}>
              {t("settings.remove")}
            </Button>
          }
        />
        {why ? <span className="text-xs text-muted-foreground">{why}</span> : null}
      </div>
    </li>
  );
}

function AddForm(props: Props) {
  const { t } = useI18n();
  const engineName = useEngineName();
  const { add: a, setAdd } = props;
  const engs = Object.keys(props.draft.cfg.engines);
  return (
    <Card className="gap-3 py-4" data-section="add-project">
      <CardContent className="grid gap-3 px-4">
        <h3 className="font-semibold">{t("settings.addTitle")}</h3>
        <div className="grid grid-cols-2 gap-3">
          <Field id="add-name" label={t("settings.name")}>
            <Input id="add-name" value={a.name} onChange={(ev) => setAdd({ ...a, name: ev.target.value })} data-sf="add-name" />
          </Field>
          <Field id="add-group" label={t("settings.group")} help={t("help.group")}>
            <Input id="add-group" value={a.group} onChange={(ev) => setAdd({ ...a, group: ev.target.value })} data-sf="add-group" />
          </Field>
        </div>
        <Field id="add-path" label={t("settings.path")}>
          <Input id="add-path" value={a.path} onChange={(ev) => setAdd({ ...a, path: ev.target.value })} data-sf="add-path" className="font-mono text-xs" />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          {engs.map((e) => (
            <Field key={e} id={`add-cap-${e}`} label={`${engineName(e)} (%)`} help={t("help.weeklyCap")}>
              <Input
                id={`add-cap-${e}`}
                type="number"
                min={0}
                max={100}
                step="any"
                value={a.caps[e] ?? ""}
                onChange={(ev) => setAdd({ ...a, caps: { ...a.caps, [e]: ev.target.value } })}
                data-sf={`add-cap:${e}`}
              />
            </Field>
          ))}
        </div>
        <div>
          <ActionButton
            data-sf="add-go"
            action={async () => {
              if (!a.name.trim() || !a.path.trim()) return void toast.error(t("settings.addMissing"));
              if (!/^[A-Za-z0-9_-]+$/.test(a.name.trim())) return void toast.error(t("settings.addBadName"));
              if (!a.path.trim().startsWith("/")) return void toast.error(t("settings.addBadPath"));
              const caps = readCaps(a.caps);
              if (!caps) return void toast.error(t("caps.invalid"));
              const body: Record<string, unknown> = { name: a.name.trim(), path: a.path.trim() };
              if (a.group.trim()) body.docker_group = a.group.trim();
              if (Object.keys(caps).length) body.weekly_cap = caps;
              await send("/api/projects", body);
              toast.success(t("settings.added", { project: body.name as string }));
              await props.onAdded(body.name as string);
            }}
          >
            {t("settings.add")}
          </ActionButton>
        </div>
      </CardContent>
    </Card>
  );
}

export function ProjectsTab(props: Props) {
  const { t } = useI18n();
  return (
    <div className="grid gap-4">
      <p className="text-sm text-muted-foreground">{t("settings.hint")}</p>
      <ol className="grid gap-3">
        {props.draft.order.map((n, i) => (
          <ProjectRow key={n} n={n} i={i} props={props} />
        ))}
      </ol>
      <AddForm {...props} />
    </div>
  );
}
