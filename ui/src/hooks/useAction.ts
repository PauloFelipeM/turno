import { useCallback, useRef, useState } from "react";
import { toast } from "sonner";
import { useI18n } from "@/i18n";

/**
 * Runs a request with its control disabled until the server answers: success shows `ok` (a text, or a
 * function read after the request, so a language switched by it is used), a failure shows the error's text.
 * Resolves to whether it worked.
 */
export function useAction() {
  const { errorText } = useI18n();
  const [pending, setPending] = useState(false);
  const running = useRef(false);
  const run = useCallback(
    async (fn: () => Promise<unknown>, ok?: string | (() => string)): Promise<boolean> => {
      if (running.current) return false;
      running.current = true;
      setPending(true);
      try {
        await fn();
        if (ok) toast.success(typeof ok === "function" ? ok() : ok);
        return true;
      } catch (e) {
        toast.error(errorText(e));
        return false;
      } finally {
        running.current = false;
        setPending(false);
      }
    },
    [errorText],
  );
  return { pending, run };
}
