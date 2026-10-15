import { useEffect, useState } from "react";
import { BellIcon } from "lucide-react";
import { enc, send, type Decision, type Overview } from "@/api";
import { ActionButton } from "@/components/ActionButton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useAction } from "@/hooks/useAction";
import { useI18n } from "@/i18n";
import { when } from "@/lib/format";

export type AttentionItem =
  | { kind: "decision"; project: string; d: Decision }
  | { kind: "blocked"; project: string; summary: string | null }
  | { kind: "setup"; project: string };

/** What needs the owner: open decisions, blocked projects and setup drafts to review (never a retry wait). */
export function attentionItems(o: Overview): AttentionItem[] {
  const items: AttentionItem[] = [];
  for (const p of o.projects) {
    for (const d of p.decisions_open || []) items.push({ kind: "decision", project: p.name, d });
    if (Object.values(p.engines).some((e) => e.status === "blocked")) items.push({ kind: "blocked", project: p.name, summary: p.blocked_summary });
    if (p.setup === "review") items.push({ kind: "setup", project: p.name });
  }
  return items;
}

function DecisionItem({ project, d, comment, onComment, onAnswered }: {
  project: string;
  d: Decision;
  comment: string;
  onComment: (v: string) => void;
  onAnswered: () => void;
}) {
  const { t, lang } = useI18n();
  const { pending, run } = useAction();
  return (
    <article className="grid gap-2 rounded-lg border bg-background p-3" data-decision={`${project}/${d.id}`}>
      <p className="text-sm">
        <b>{project}</b> · {d.question}
      </p>
      {d.context ? <p className="text-xs text-muted-foreground whitespace-pre-wrap">{d.context}</p> : null}
      {d.deadline ? <p className="text-xs text-muted-foreground">{t("project.deadline", { when: when(lang, d.deadline) })}</p> : null}
      <Input
        maxLength={2000}
        value={comment}
        onChange={(ev) => onComment(ev.target.value)}
        aria-label={t("attention.comment")}
        placeholder={t("attention.comment")}
      />
      <div className="flex flex-wrap gap-2">
        {d.options.map((o) => (
          <Button
            key={o.id}
            size="sm"
            variant={o.id === d.default ? "default" : "outline"}
            disabled={pending}
            data-option={o.id}
            className="h-auto min-h-8 whitespace-normal py-1.5 text-left"
            onClick={async () => {
              const ok = await run(
                () => send(`/api/decisions/${enc(project)}/${enc(d.id)}`, { option: o.id, comment: comment.trim() || undefined }),
                t("attention.answered"),
              );
              if (ok) onAnswered();
            }}
          >
            {`${o.id.toUpperCase()}. ${o.label}`}
            {o.id === d.default ? <Badge variant="secondary" className="ml-1">{t("project.default")}</Badge> : null}
          </Button>
        ))}
      </div>
    </article>
  );
}

export function NeedsYou({ overview, onReview }: { overview: Overview; onReview: (project: string) => void }) {
  const { t } = useI18n();
  const items = attentionItems(overview);
  // Comments typed in the strip survive the overview events; those of answered decisions are dropped.
  const [comments, setComments] = useState<Record<string, string>>({});
  const live = items.filter((i) => i.kind === "decision").map((i) => (i.kind === "decision" ? `${i.project}/${i.d.id}` : ""));
  const liveKey = live.join("\n");
  useEffect(() => {
    const keep = new Set(liveKey.split("\n"));
    setComments((c) => {
      const next = Object.fromEntries(Object.entries(c).filter(([k]) => keep.has(k)));
      return Object.keys(next).length === Object.keys(c).length ? c : next;
    });
  }, [liveKey]);
  if (!items.length) return null;
  return (
    <Card className="gap-3 border-warning/50 bg-warning/5" aria-live="polite" data-section="attention">
      <CardHeader className="items-center">
        <BellIcon className="size-4 text-[color:var(--warning)]" />
        <CardTitle className="text-base">{t("attention.title")}</CardTitle>
        <Badge variant="warning">{items.length}</Badge>
      </CardHeader>
      <CardContent className="grid gap-3 md:grid-cols-2">
        {items.map((i) => {
          if (i.kind === "decision") {
            const key = `${i.project}/${i.d.id}`;
            return (
              <DecisionItem
                key={key}
                project={i.project}
                d={i.d}
                comment={comments[key] ?? ""}
                onComment={(v) => setComments((c) => ({ ...c, [key]: v }))}
                onAnswered={() => setComments(({ [key]: _gone, ...rest }) => rest)}
              />
            );
          }
          if (i.kind === "blocked") {
            return (
              <article key={`blocked:${i.project}`} className="grid gap-2 rounded-lg border bg-background p-3">
                <p className="text-sm">
                  <b>{i.project}</b> · {t("attention.blocked")}
                </p>
                <p className="text-xs text-muted-foreground whitespace-pre-wrap">{i.summary || t("attention.noSummary")}</p>
                <div>
                  <ActionButton
                    size="sm"
                    variant="outline"
                    data-action={`reset:${i.project}`}
                    action={() => send(`/api/projects/${enc(i.project)}/reset`, {})}
                    ok={t("attention.reactivated", { project: i.project })}
                  >
                    {t("attention.reactivate")}
                  </ActionButton>
                </div>
              </article>
            );
          }
          return (
            <article key={`setup:${i.project}`} className="grid gap-2 rounded-lg border bg-background p-3">
              <p className="text-sm">
                <b>{i.project}</b> · {t("setup.attention")}
              </p>
              <div>
                <Button size="sm" variant="outline" data-action={`setup-review:${i.project}`} onClick={() => onReview(i.project)}>
                  {t("setup.review")}
                </Button>
              </div>
            </article>
          );
        })}
      </CardContent>
    </Card>
  );
}
