import * as React from "react";
import { Progress as ProgressPrimitive } from "radix-ui";
import { cn } from "@/lib/utils";

type Props = React.ComponentPropsWithoutRef<typeof ProgressPrimitive.Root> & {
  /** A mark on the track (0 to 100), e.g. the engine's cap. */
  marker?: number | null;
  markerLabel?: string;
  indicatorClassName?: string;
};

export const Progress = React.forwardRef<React.ElementRef<typeof ProgressPrimitive.Root>, Props>(
  ({ className, value, marker, markerLabel, indicatorClassName, ...props }, ref) => (
    <ProgressPrimitive.Root
      ref={ref}
      data-slot="progress"
      value={value}
      className={cn("relative h-2 w-full overflow-visible rounded-full bg-primary/15", className)}
      {...props}
    >
      <span className="absolute inset-0 overflow-hidden rounded-full">
        <ProgressPrimitive.Indicator
          className={cn("h-full w-full flex-1 bg-primary transition-transform", indicatorClassName)}
          style={{ transform: `translateX(-${100 - Math.min(100, Math.max(0, value ?? 0))}%)` }}
        />
      </span>
      {marker != null ? (
        <span
          aria-hidden="true"
          title={markerLabel}
          className="absolute -top-1 h-4 w-0.5 rounded bg-foreground/70"
          style={{ left: `calc(${Math.min(100, Math.max(0, marker))}% - 1px)` }}
        />
      ) : null}
    </ProgressPrimitive.Root>
  ),
);
Progress.displayName = "Progress";
