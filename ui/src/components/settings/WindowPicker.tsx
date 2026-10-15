import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useI18n } from "@/i18n";
import { DAYS, type Day, type WindowPoint } from "@/lib/schedule";

/** One end of the window: a day-of-week Select and an <input type="time">; the parent formats "fri 19:00". */
export function WindowPointPicker({ id, label, value, onChange }: { id: string; label: string; value: WindowPoint; onChange: (v: WindowPoint) => void }) {
  const { t } = useI18n();
  return (
    <fieldset className="grid gap-1.5" data-window={id}>
      <legend className="mb-1.5 text-sm font-medium">{label}</legend>
      <div className="flex gap-2">
        <Select value={value.day} onValueChange={(day) => onChange({ ...value, day: day as Day })}>
          <SelectTrigger className="w-36" aria-label={`${label}: ${t("settings.day")}`} data-sf={`${id}-day`}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {DAYS.map((d) => (
              <SelectItem key={d} value={d} data-day={d}>
                {t(`settings.days.${d}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Label htmlFor={`${id}-time`} className="sr-only">{`${label}: ${t("settings.time")}`}</Label>
        <Input
          id={`${id}-time`}
          type="time"
          step={60}
          required
          className="w-32 tabular"
          value={value.time}
          onChange={(ev) => onChange({ ...value, time: ev.target.value })}
          data-sf={`${id}-time`}
        />
      </div>
    </fieldset>
  );
}
