"""Janela semanal em que uma instância pode trabalhar, em horário local (ex.: sexta 19:00 a domingo 03:00)."""

import datetime as dt
import re

DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
DAYS_PT = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")


def label(when):
    """'sex 02/10 19:00': o instante como o Paulo lê, sem depender do locale."""
    return f"{DAYS_PT[when.weekday()]} {when:%d/%m %H:%M}"


def parse(spec):
    """'fri 19:00' -> minutos desde segunda 00:00."""
    m = re.fullmatch(r"\s*(mon|tue|wed|thu|fri|sat|sun)\s+(\d{1,2}):(\d{2})\s*", str(spec).lower())
    if not m or int(m.group(2)) > 23 or int(m.group(3)) > 59:
        raise ValueError(f"horário inválido: {spec!r} (use, por exemplo, 'fri 19:00')")
    return DAYS[m.group(1)] * 1440 + int(m.group(2)) * 60 + int(m.group(3))


def minute_of_week(when):
    return when.weekday() * 1440 + when.hour * 60 + when.minute


def next_occurrence(spec, when):
    """Próximo instante depois de `when` com o dia e a hora de `spec` (texto ou minutos da semana)."""
    target = parse(spec) if isinstance(spec, str) else spec
    monday = (when - dt.timedelta(days=when.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    at = monday + dt.timedelta(minutes=target)
    if at <= when:
        at += dt.timedelta(days=7)
    return at


class Window:
    def __init__(self, start, end, margin_minutes=15):
        self.start_spec, self.end_spec = start, end
        self.start, self.end = parse(start), parse(end)
        if self.start == self.end:
            raise ValueError("a janela precisa de início e fim diferentes")
        self.margin = dt.timedelta(minutes=margin_minutes)

    def contains(self, when):
        t = minute_of_week(when)
        if self.start < self.end:
            return self.start <= t < self.end
        return t >= self.start or t < self.end  # janela que atravessa a virada da semana

    def end_of(self, when):
        """Fim da janela em que `when` está (ou da próxima, se estiver fora)."""
        return next_occurrence(self.end, when)

    def next_start(self, when):
        return when if self.contains(when) else next_occurrence(self.start, when)

    def can_start(self, when):
        """Dentro da janela e com mais que a margem até o fim: pode começar uma unidade."""
        return self.contains(when) and self.end_of(when) - when > self.margin

    def describe(self):
        def pt(minutes):
            return f"{DAYS_PT[minutes // 1440]} {minutes % 1440 // 60:02d}:{minutes % 60:02d}"
        return f"{pt(self.start)} → {pt(self.end)}"


def from_config(cfg):
    sched = cfg.get("schedule")
    if not sched:
        return None
    return Window(sched["start"], sched["end"], sched.get("start_margin_minutes", 15))
