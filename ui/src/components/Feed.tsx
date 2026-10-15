import type { FeedItem } from "@/api";
import { useI18n } from "@/i18n";
import { cn } from "@/lib/utils";

/** The step the run is in: the last subagent called, or the wait for one. */
export function currentStep(items: FeedItem[], waitText: string): string | null {
  for (let k = items.length - 1; k >= 0; k -= 1) {
    if (items[k].kind === "role") return items[k].role ?? null;
    if (items[k].kind === "wait") return waitText;
  }
  return null;
}

function Item({ i }: { i: FeedItem }) {
  const { t } = useI18n();
  switch (i.kind) {
    case "message":
      return <li className="whitespace-pre-wrap">{i.text}</li>;
    case "command":
      return <li className="font-mono text-xs text-primary">{`$ ${i.text ?? ""}`}</li>;
    case "command_done":
      return (
        <li className={cn("font-mono text-xs", i.exit_code ? "text-destructive" : "text-muted-foreground")}>
          <details>
            <summary className="cursor-pointer">{`↳ ${t("live.exit", { code: i.exit_code ?? "?" })}`}</summary>
            <pre className="mt-1 max-h-60 overflow-auto rounded bg-muted p-2 whitespace-pre-wrap">{i.output || ""}</pre>
          </details>
        </li>
      );
    case "file":
      return <li className="font-mono text-xs">{`✎ ${i.text ?? ""}`}</li>;
    case "role":
      return <li className="font-medium">{`→ ${i.role ?? ""}${i.text ? `: ${i.text}` : ""}`}</li>;
    case "wait":
      return <li className="text-muted-foreground">{`… ${t("live.wait")}`}</li>;
    case "error":
      return <li className="text-destructive">{`! ${i.text ?? ""}`}</li>;
    default:
      return <li className="text-muted-foreground">{t("live.unknown", { text: i.text })}</li>;
  }
}

export function FeedList({ items, className }: { items: FeedItem[]; className?: string }) {
  return (
    <ol className={cn("grid gap-1.5 text-sm break-words", className)}>
      {items.map((i) => (
        <Item key={i.n} i={i} />
      ))}
    </ol>
  );
}
