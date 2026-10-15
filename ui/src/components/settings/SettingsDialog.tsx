import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { getJSON, send, type ConfigView } from "@/api";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { textIn, useI18n } from "@/i18n";
import { buildDraft, emptyAdd, mergeDraft, saveSteps, type AddDraft, type Draft } from "./draft";
import { GeneralTab } from "./GeneralTab";
import { ProjectsTab, type SetupBoxState } from "./ProjectsTab";

export type SettingsTab = "projects" | "engines" | "general";

interface Props {
  open: boolean;
  tab: SettingsTab;
  onTab: (tab: SettingsTab) => void;
  onOpenChange: (open: boolean) => void;
  /** A saved language applies at once to the whole page. */
  onLanguage: (lang: string) => void;
}

/** Settings: a working copy of GET /api/config, edited in three tabs; "Save" sends only what changed. */
export function SettingsDialog({ open, tab, onTab, onOpenChange, onLanguage }: Props) {
  const { t, errorText } = useI18n();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [add, setAdd] = useState<AddDraft>(emptyAdd);
  const [setup, setSetup] = useState<SetupBoxState>({ open: null, goal: "" });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) {
      setDraft(null);
      return;
    }
    let alive = true;
    getJSON<ConfigView>("/api/config")
      .then((cfg) => alive && setDraft(buildDraft(cfg)))
      .catch((e) => {
        if (!alive) return;
        toast.error(errorText(e));
        onOpenChange(false);
      });
    return () => {
      alive = false;
    };
  }, [open]);

  const update = useCallback((fn: (d: Draft) => void) => {
    setDraft((d) => {
      if (!d) return d;
      const { cfg, ...rest } = d;
      const next = { cfg, ...structuredClone(rest) } as Draft;
      fn(next);
      return next;
    });
  }, []);

  const refresh = useCallback(async () => {
    const cfg = await getJSON<ConfigView>("/api/config");
    setDraft((d) => mergeDraft(d, cfg));
  }, []);

  const onAdded = useCallback(async (name: string) => {
    const cfg = await getJSON<ConfigView>("/api/config");
    setDraft((d) => mergeDraft(d, cfg));
    setAdd(emptyAdd());
    const added = cfg.projects.find((x) => x.name === name);
    // A project without docs/autopilot.md gets the "Preparar com um agente" box right away.
    if (added && added.checks.includes("no_autopilot_doc") && !added.checks.includes("not_git")) setSetup({ open: name, goal: "" });
  }, []);

  const save = async () => {
    if (!draft) return;
    const steps = saveSteps(draft);
    if (!steps) return void toast.error(t("settings.invalid"));
    if (!steps.length) return void toast(t("caps.nothing"));
    setSaving(true);
    try {
      for (const s of steps) await send(s.url, s.body, "PUT");
      const cfg = await getJSON<ConfigView>("/api/config");
      setDraft(buildDraft(cfg));
      onLanguage(cfg.language);
      toast.success(textIn(cfg.language, "settings.saved"));
    } catch (e) {
      toast.error(errorText(e));
      // Steps already saved stay saved; the draft keeps what the owner typed (the PUTs are idempotent).
      await refresh().catch(() => undefined);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent closeLabel={t("settings.close")} className="flex max-h-[calc(100dvh-2rem)] flex-col gap-0 p-0 sm:max-w-3xl" data-dialog="settings">
        <DialogHeader className="border-b p-6 pb-4">
          <DialogTitle>{t("settings.title")}</DialogTitle>
          <DialogDescription className="sr-only">{t("settings.hint")}</DialogDescription>
        </DialogHeader>
        <Tabs value={tab} onValueChange={(v) => onTab(v as SettingsTab)} className="flex min-h-0 flex-1 flex-col">
          <div className="px-6 pt-4">
            <TabsList className="w-full sm:w-fit">
              <TabsTrigger value="projects" data-sf="tab:projects">
                {t("settings.projects")}
              </TabsTrigger>
              <TabsTrigger value="general" data-sf="tab:general">
                {t("settings.general")}
              </TabsTrigger>
            </TabsList>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
            {!draft ? (
              <div className="grid gap-3">
                <Skeleton className="h-24 w-full" />
                <Skeleton className="h-24 w-full" />
              </div>
            ) : (
              <>
                <TabsContent value="projects">
                  <ProjectsTab draft={draft} update={update} add={add} setAdd={setAdd} setup={setup} setSetup={setSetup} refresh={refresh} onAdded={onAdded} />
                </TabsContent>
                <TabsContent value="general">
                  <GeneralTab draft={draft} update={update} />
                </TabsContent>
              </>
            )}
          </div>
        </Tabs>
        <DialogFooter className="border-t p-4">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("settings.close")}
          </Button>
          <Button onClick={save} disabled={!draft || saving} data-sf="save">
            {t("settings.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
