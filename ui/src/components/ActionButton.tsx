import * as React from "react";
import { Button, type ButtonProps } from "@/components/ui/button";
import { useAction } from "@/hooks/useAction";

type Props = Omit<ButtonProps, "onClick"> & {
  /** The request; the button stays disabled until it settles. */
  action: () => Promise<unknown>;
  /** The toast on success. */
  ok?: string | (() => string);
  /** Called with the outcome, after the toast. */
  onDone?: (ok: boolean) => void;
};

export const ActionButton = React.forwardRef<HTMLButtonElement, Props>(({ action, ok, onDone, disabled, ...props }, ref) => {
  const { pending, run } = useAction();
  return (
    <Button
      ref={ref}
      disabled={disabled || pending}
      aria-busy={pending || undefined}
      onClick={async () => {
        const done = await run(action, ok);
        onDone?.(done);
      }}
      {...props}
    />
  );
});
ActionButton.displayName = "ActionButton";
