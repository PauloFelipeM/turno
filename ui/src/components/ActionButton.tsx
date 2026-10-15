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

export const ActionButton = React.forwardRef<HTMLButtonElement, Props>(({ action, ok, onDone, disabled, ...rest }, ref) => {
  const { pending, run } = useAction();
  // A wrapper such as TooltipTrigger (asChild) passes its own onClick in the props at runtime; spread
  // first and compose, or that handler replaces the action and the click does nothing.
  const { onClick: wrapperClick, ...props } = rest as typeof rest & { onClick?: React.MouseEventHandler<HTMLButtonElement> };
  return (
    <Button
      ref={ref}
      {...props}
      disabled={disabled || pending}
      aria-busy={pending || undefined}
      onClick={async (ev) => {
        wrapperClick?.(ev);
        const done = await run(action, ok);
        onDone?.(done);
      }}
    />
  );
});
ActionButton.displayName = "ActionButton";
