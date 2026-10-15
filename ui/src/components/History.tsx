import { useEffect, useState } from "react";
import { enc, getJSON, type Unit } from "@/api";
import { useEngineName } from "@/components/Engines";
import { FeedList } from "@/components/Feed";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useUnits } from "@/hooks/useUnits";
import { useI18n } from "@/i18n";
import { clock, duration, pct } from "@/lib/format";

const OUTCOME: Record<string, BadgeProps["variant"]> = {
  worked: "success",
  waiting: "secondary",
  idle: "secondary",
  blocked: "destructive",
  usage_limit: "warning",
  error: "destructive",
  cut: "warning",
  unfinished: "warning",
  stopped: "warning",
};

export function OutcomeBadge({ outcome }: { outcome: string }) {
  const { t } = useI18n();
  return (
    <Badge variant={OUTCOME[outcome] ?? "outline"} data-outcome={outcome}>
      {t(`outcome.${outcome}`)}
    </Badge>
  );
}

function UnitDialog({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { t, lang } = useI18n();
  const engineName = useEngineName();
  const [loaded, setLoaded] = useState<{ id: string; unit: Unit | null; failed: boolean } | null>(null);
  useEffect(() => {
    if (!id) return;
    let alive = true;
    setLoaded({ id, unit: null, failed: false });
    getJSON<Unit>(`/api/units/${enc(id)}`)
      .then((unit) => alive && setLoaded({ id, unit, failed: false }))
      .catch(() => alive && setLoaded({ id, unit: null, failed: true }));
    return () => {
      alive = false;
    };
  }, [id]);
  const u = loaded?.id === id ? loaded.unit : null;
  const failed = loaded?.id === id && loaded.failed;
  return (
    <Dialog open={!!id} onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={t("unit.close")} className="sm:max-w-3xl" data-dialog="unit">
        {failed ? (
          <DialogHeader>
            <DialogTitle>{t("loadError")}</DialogTitle>
          </DialogHeader>
        ) : !u ? (
          <div className="grid gap-3">
            <DialogTitle className="sr-only">{t("app.loading")}</DialogTitle>
            <Skeleton className="h-6 w-2/3" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : (
          <>
            <DialogHeader>
              <DialogTitle>{`${u.project} · ${u.unit || t(`outcome.${u.outcome}`)}`}</DialogTitle>
              <DialogDescription asChild>
                <div className="grid gap-1">
                  {u.milestone ? (
                    <p>
                      <b>{`${t("unit.milestone")}: `}</b>
                      {u.milestone}
                    </p>
                  ) : null}
                  {u.summary ? <p className="whitespace-pre-wrap text-foreground">{u.summary}</p> : null}
                  {u.cost ? <p>{t("unit.cost", { primary: pct(lang, u.cost.primary), secondary: pct(lang, u.cost.secondary) })}</p> : null}
                </div>
              </DialogDescription>
            </DialogHeader>
            <h3 className="font-semibold">{t("unit.runs")}</h3>
            {u.runs.map((r, k) => (
              <section key={r.run ?? k} className="grid gap-2 rounded-lg border p-3">
                <p className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="tabular">{`${clock(lang, r.started_at)} → ${clock(lang, r.ended_at)}`}</span>
                  <span>{`· ${engineName(r.engine)} ·`}</span>
                  <OutcomeBadge outcome={r.outcome} />
                </p>
                {r.feed ? <FeedList items={r.feed} className="max-h-96 overflow-auto" /> : <p className="text-xs text-muted-foreground">{t("unit.feedGone")}</p>}
              </section>
            ))}
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** The unit history as a table; with `project`, only that project's units (the project panel). */
export function History({ tick, project = null, compact = false }: { tick: number; project?: string | null; compact?: boolean }) {
  const { t, lang } = useI18n();
  const engineName = useEngineName();
  const { units, exhausted, error, loaded, more } = useUnits(project, tick);
  const [open, setOpen] = useState<string | null>(null);
  return (
    <section aria-labelledby={compact ? undefined : "history-h"} className="grid gap-3" data-section={compact ? "project-history" : "history"}>
      {compact ? null : (
        <h2 id="history-h" className="text-sm font-semibold tracking-wide text-muted-foreground uppercase">
          {t("history.title")}
        </h2>
      )}
      <Card className="py-2">
        <CardContent className="px-2">
          {error && !units.length ? (
            <p className="p-3 text-sm text-destructive">{t("loadError")}</p>
          ) : !loaded ? (
            <div className="grid gap-2 p-2">
              {[0, 1, 2].map((k) => (
                <Skeleton key={k} className="h-8 w-full" />
              ))}
            </div>
          ) : !units.length ? (
            <p className="p-3 text-sm text-muted-foreground">{t("history.empty")}</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("history.when")}</TableHead>
                  {compact ? null : <TableHead>{t("history.project")}</TableHead>}
                  <TableHead>{t("history.outcome")}</TableHead>
                  <TableHead className="min-w-48">{t("history.unit")}</TableHead>
                  <TableHead className="hidden md:table-cell">{t("history.engines")}</TableHead>
                  <TableHead className="hidden sm:table-cell">{t("history.duration")}</TableHead>
                  <TableHead className="text-right">{t("history.cost")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {units.map((u) => (
                  <TableRow
                    key={u.unit_id}
                    data-unit={u.unit_id}
                    tabIndex={0}
                    className="cursor-pointer focus-visible:bg-muted focus-visible:outline-none"
                    onClick={() => setOpen(u.unit_id)}
                    onKeyDown={(ev) => {
                      if (ev.key === "Enter" || ev.key === " ") {
                        ev.preventDefault();
                        setOpen(u.unit_id);
                      }
                    }}
                  >
                    <TableCell className="whitespace-nowrap text-muted-foreground tabular">{clock(lang, u.started_at)}</TableCell>
                    {compact ? null : <TableCell className="font-medium">{u.project}</TableCell>}
                    <TableCell>
                      <OutcomeBadge outcome={u.outcome} />
                    </TableCell>
                    <TableCell className="max-w-[28rem] truncate" title={u.unit ?? undefined}>{u.unit || ""}</TableCell>
                    <TableCell className="hidden whitespace-nowrap text-muted-foreground md:table-cell">{u.engines.map(engineName).join(" → ")}</TableCell>
                    <TableCell className="hidden whitespace-nowrap text-muted-foreground sm:table-cell">{duration(u.duration)}</TableCell>
                    <TableCell className="text-right tabular">{u.cost ? pct(lang, u.cost.secondary) : ""}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {!exhausted && units.length ? (
            <div className="flex justify-center p-2">
              <Button variant="ghost" size="sm" onClick={() => void more()} data-action="history-more">
                {t("history.more")}
              </Button>
            </div>
          ) : null}
        </CardContent>
      </Card>
      <UnitDialog id={open} onClose={() => setOpen(null)} />
    </section>
  );
}
