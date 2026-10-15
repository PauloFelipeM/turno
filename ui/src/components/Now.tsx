import { useEffect, useLayoutEffect, useRef } from "react";
import type { FeedItem, LiveRun, Overview } from "@/api";
import { phaseText, useEngineName } from "@/components/Engines";
import { currentStep, FeedList } from "@/components/Feed";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useI18n } from "@/i18n";
import { clock } from "@/lib/format";

const SHOWN = 200;

function RunCard({ run, items }: { run: LiveRun; items: FeedItem[] }) {
  const { t, lang } = useI18n();
  const engineName = useEngineName();
  const viewport = useRef<HTMLDivElement>(null);
  const atEnd = useRef(true);
  const step = currentStep(items, t("live.wait"));
  // Follows the end of the feed unless the owner scrolled up to read.
  useLayoutEffect(() => {
    const v = viewport.current;
    if (v && atEnd.current) v.scrollTop = v.scrollHeight;
  }, [items.length]);
  useEffect(() => {
    const v = viewport.current;
    if (v) v.scrollTop = v.scrollHeight;
  }, []);
  return (
    <Card className="gap-3" data-run={run.run}>
      <CardHeader className="items-center">
        <span className="size-2 animate-pulse rounded-full bg-success" aria-hidden="true" />
        <CardTitle className="text-base">{`${run.project} · ${engineName(run.engine)}`}</CardTitle>
        {step ? <Badge variant="secondary">{step}</Badge> : null}
        <span className="ml-auto text-xs text-muted-foreground tabular">{clock(lang, run.started_at)}</span>
      </CardHeader>
      <CardContent>
        <ScrollArea
          className="h-80 rounded-md border bg-muted/30"
          viewportRef={viewport}
          onViewportScroll={(ev) => {
            const v = ev.currentTarget;
            atEnd.current = v.scrollHeight - v.scrollTop - v.clientHeight <= 24;
          }}
        >
          <div aria-live="polite" className="p-3">
            <FeedList items={items.slice(-SHOWN)} />
          </div>
        </ScrollArea>
      </CardContent>
    </Card>
  );
}

export function Now({ overview, feeds }: { overview: Overview; feeds: Record<string, FeedItem[]> }) {
  const { t, lang } = useI18n();
  return (
    <section aria-labelledby="live-h" className="grid gap-3">
      <h2 id="live-h" className="text-sm font-semibold tracking-wide text-muted-foreground uppercase">
        {t("live.title")}
      </h2>
      {overview.live.length ? (
        overview.live.map((l) => <RunCard key={l.run} run={l} items={feeds[l.run] ?? []} />)
      ) : (
        <Card className="gap-1 py-4">
          <CardContent className="grid gap-1">
            <p className="text-sm text-muted-foreground">{t("live.none")}</p>
            {overview.engines.map((e) => (
              <p key={e.engine} className="text-xs text-muted-foreground">{`${e.label}: ${phaseText(t, lang, e)}`}</p>
            ))}
          </CardContent>
        </Card>
      )}
    </section>
  );
}
