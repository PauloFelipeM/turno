import { useState, type ReactElement } from "react";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useAction } from "@/hooks/useAction";
import { useI18n } from "@/i18n";

interface Props {
  /** The element that opens the confirmation (a Button). */
  trigger: ReactElement;
  title: string;
  description: string;
  confirm: string;
  action: () => Promise<unknown>;
  ok?: string | (() => string);
  onDone?: (ok: boolean) => void;
  /** A tooltip on the trigger (icon buttons). */
  tooltip?: string;
  "data-action"?: string;
}

/** A destructive action behind an AlertDialog; the dialog closes when the request worked. */
export function Confirm({ trigger, title, description, confirm, action, ok, onDone, tooltip, ...rest }: Props) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const { pending, run } = useAction();
  return (
    <AlertDialog open={open} onOpenChange={setOpen}>
      {tooltip ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <AlertDialogTrigger asChild>{trigger}</AlertDialogTrigger>
          </TooltipTrigger>
          <TooltipContent>{tooltip}</TooltipContent>
        </Tooltip>
      ) : (
        <AlertDialogTrigger asChild>{trigger}</AlertDialogTrigger>
      )}
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{t("cancel")}</AlertDialogCancel>
          <Button
            variant="destructive"
            disabled={pending}
            data-action={rest["data-action"]}
            onClick={async () => {
              const done = await run(action, ok);
              if (done) setOpen(false);
              onDone?.(done);
            }}
          >
            {confirm}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
