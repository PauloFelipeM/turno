import { useEffect, useState } from "react";
import { WifiOffIcon } from "lucide-react";
import { Engines } from "@/components/Engines";
import { History } from "@/components/History";
import { NeedsYou } from "@/components/NeedsYou";
import { Now } from "@/components/Now";
import { Projects } from "@/components/Projects";
import { ProjectSheet } from "@/components/ProjectSheet";
import { SettingsDialog, type SettingsTab } from "@/components/settings/SettingsDialog";
import { TopBar } from "@/components/TopBar";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { Toaster } from "@/components/ui/sonner";
import { useOverview } from "@/hooks/useOverview";
import { useI18n } from "@/i18n";

function Loading() {
  const { t } = useI18n();
  return (
    <div className="grid gap-6" aria-busy="true" aria-label={t("app.loading")}>
      <div className="grid gap-4 sm:grid-cols-2">
        <Skeleton className="h-48" />
        <Skeleton className="h-48" />
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <Skeleton className="h-36" />
        <Skeleton className="h-36" />
        <Skeleton className="h-36" />
      </div>
    </div>
  );
}

export function App() {
  const { t, setLang } = useI18n();
  const { overview, feeds, connected, tick } = useOverview();
  const [project, setProject] = useState<string | null>(null);
  const [settings, setSettings] = useState<{ open: boolean; tab: SettingsTab }>({ open: false, tab: "projects" });

  // The language comes from the config; changed in another tab or by hand, the page follows.
  const language = overview?.language;
  useEffect(() => {
    if (language) setLang(language);
  }, [language, setLang]);

  // A project removed while its panel is open closes the panel.
  useEffect(() => {
    if (project && overview && !overview.projects.some((p) => p.name === project)) setProject(null);
  }, [overview, project]);

  return (
    <div className="min-h-dvh bg-background">
      <TopBar overview={overview} onSettings={() => setSettings({ open: true, tab: settings.tab })} />
      {!connected ? (
        <div className="mx-auto max-w-7xl px-4 pt-4">
          <Alert variant="destructive" data-banner="offline" role="status">
            <WifiOffIcon />
            <AlertDescription className="text-current">{t("offline")}</AlertDescription>
          </Alert>
        </div>
      ) : null}
      <main className="mx-auto grid max-w-7xl gap-8 px-4 py-6">
        {!overview ? (
          <Loading />
        ) : (
          <>
            <Engines overview={overview} />
            <NeedsYou overview={overview} onReview={setProject} />
            <Projects overview={overview} onOpen={setProject} onAdd={() => setSettings({ open: true, tab: "projects" })} />
            <Now overview={overview} feeds={feeds} />
            <History tick={tick} />
            <ProjectSheet name={project} overview={overview} tick={tick} onClose={() => setProject(null)} />
          </>
        )}
      </main>
      <SettingsDialog
        open={settings.open}
        tab={settings.tab}
        onTab={(tab) => setSettings((s) => ({ ...s, tab }))}
        onOpenChange={(open) => setSettings((s) => ({ ...s, open }))}
        onLanguage={setLang}
      />
      <Toaster />
    </div>
  );
}
