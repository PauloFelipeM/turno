"""What the scheduler writes for the owner (phone notifications, the summary of a failed setup unit,
the window's day names), in each of the owner's languages.

Code, logs and CLI output stay in English; only owner-facing texts go through `text`. Another
language is one more table with exactly the keys and fields of the pt-BR one (a test checks it),
plus its name in `NAMES` and its code in `config.LANGUAGES`.
"""

DEFAULT = "en"

# How the prompts name the language the agents write the owner's texts in.
NAMES = {"pt-BR": "Brazilian Portuguese (pt-BR)", "en": "English (en)"}

TEXTS = {
    "pt-BR": {
        "answered_title": "{project}: anotado",
        "answered_body": "{question}\n→ {label}",
        "note_title": "autopilot: mensagem anotada",
        "note_body": "Para {names}, na próxima unidade:\n{text}",
        "no_question_title": "autopilot: nenhuma pergunta aberta",
        "no_question_body": "Não há decisão esperando resposta. Ignorei: {text}",
        "which_title": "autopilot: qual pergunta?",
        "which_body": "Há {n} perguntas abertas; {hint}.",
        "which_hint_project": "ponha o projeto antes da letra, por exemplo: {project} {letter}",
        "which_hint_buttons": "use os botões da notificação",
        "bad_option_title": "{project}: opção inexistente",
        "bad_option_body": "{question}\nAs opções são: {options}.",
        "decision_title": "{project}: decisão",
        "decision_how": "Responda pelos botões ou digitando a letra (ex.: {letter}).",
        "decision_default": "Sem resposta em {hours} h: {label}",
        "timeout_title": "{project}: sem resposta",
        "timeout_body": "{question}\n→ {label} (padrão)",
        "setup_ready_title": "Rascunho do {project} pronto para revisar",
        "setup_ready_body": "Abra o painel para aprovar ou pedir mudanças.",
        "setup_missing_draft": "o rascunho (setup/autopilot.md)",
        "setup_missing_doc": "o docs/autopilot.md no lugar",
        "setup_failed": "A unidade de preparo terminou sem {missing}. {summary}",
        "errors_title": "{project}: erros seguidos",
        "errors_body": "{n} execuções com erro. Último log: {err}",
        "delivered_title": "{project}: mensagem entregue ao {engine}",
        "delivered_body": "{n} mensagem(ns) lida(s) nesta unidade: {unit}",
        "idle_title": "{project}: sem trabalho",
        "idle_body": "Backlog concluído.",
        "blocked_title": "{project}: bloqueado",
        "milestone_title": "{project}: {milestone}",
        "window_open_title": "{engine}: começou o fim de semana",
        "window_open_body": "Cota semanal em {used}%; teto {cap}%.",
        "window_closed_title": "{engine}: fim de semana encerrado",
        "window_closed_body": "Cota semanal em {used}%. {tail}",
        "window_baton": "Ficaram com o bastão: {names}.",
        "window_clean": "Nenhuma unidade ficou pela metade.",
        "all_idle_title": "{engine}: fim",
        "all_idle_body": "Todos os projetos terminaram o backlog.",
        "docker_title": "{engine}: Docker com problema",
        "docker_body": "Não consegui subir os containers de {project} ({reason}). Veja o log.",
        "docker_wsl_title": "{engine}: Docker sem integração com o WSL",
        "docker_wsl_body": "O Docker Desktop está rodando, mas o WSL não o alcança. Ligue Settings > Resources > "
                           "WSL integration para a sua distro, ou reinicie o Docker Desktop. Tento de novo a cada 10 min.",
        "conflict_title": "{engine}: projeto em conflito",
        "conflict_body": "{project} tem unidade aberta nos dois autopilots. Veja os logs.",
        "test_title": "autopilot: teste",
        "test_body": "Se você está lendo isto no celular, as notificações funcionam.",
        "crash_title": "autopilot: parou",
        "crash_body": "Erro inesperado. Veja o fim do autopilot.log.",
        "day_0": "seg", "day_1": "ter", "day_2": "qua", "day_3": "qui", "day_4": "sex", "day_5": "sáb", "day_6": "dom",
    },
    "en": {
        "answered_title": "{project}: noted",
        "answered_body": "{question}\n→ {label}",
        "note_title": "autopilot: message noted",
        "note_body": "For {names}, in the next unit:\n{text}",
        "no_question_title": "autopilot: no open question",
        "no_question_body": "No decision is waiting for an answer. Ignored: {text}",
        "which_title": "autopilot: which question?",
        "which_body": "There are {n} open questions; {hint}.",
        "which_hint_project": "put the project before the letter, for example: {project} {letter}",
        "which_hint_buttons": "use the notification's buttons",
        "bad_option_title": "{project}: no such option",
        "bad_option_body": "{question}\nThe options are: {options}.",
        "decision_title": "{project}: decision",
        "decision_how": "Answer with the buttons or by typing the letter (e.g. {letter}).",
        "decision_default": "No answer in {hours} h: {label}",
        "timeout_title": "{project}: no answer",
        "timeout_body": "{question}\n→ {label} (default)",
        "setup_ready_title": "{project} draft ready for review",
        "setup_ready_body": "Open the panel to approve it or ask for changes.",
        "setup_missing_draft": "the draft (setup/autopilot.md)",
        "setup_missing_doc": "docs/autopilot.md in place",
        "setup_failed": "The setup unit ended without {missing}. {summary}",
        "errors_title": "{project}: repeated errors",
        "errors_body": "{n} runs failed. Last log: {err}",
        "delivered_title": "{project}: message delivered to {engine}",
        "delivered_body": "{n} message(s) read in this unit: {unit}",
        "idle_title": "{project}: no work left",
        "idle_body": "Backlog finished.",
        "blocked_title": "{project}: blocked",
        "milestone_title": "{project}: {milestone}",
        "window_open_title": "{engine}: the weekend started",
        "window_open_body": "Weekly quota at {used}%; cap {cap}%.",
        "window_closed_title": "{engine}: the weekend is over",
        "window_closed_body": "Weekly quota at {used}%. {tail}",
        "window_baton": "Left with the baton: {names}.",
        "window_clean": "No unit was left half-done.",
        "all_idle_title": "{engine}: done",
        "all_idle_body": "Every project finished its backlog.",
        "docker_title": "{engine}: Docker problem",
        "docker_body": "Could not bring up the containers of {project} ({reason}). See the log.",
        "docker_wsl_title": "{engine}: Docker without WSL integration",
        "docker_wsl_body": "Docker Desktop is running, but WSL cannot reach it. Turn on Settings > Resources > "
                           "WSL integration for your distro, or restart Docker Desktop. Retrying every 10 min.",
        "conflict_title": "{engine}: project in conflict",
        "conflict_body": "{project} has an open unit in both loops. See the logs.",
        "test_title": "autopilot: test",
        "test_body": "If you are reading this on your phone, notifications work.",
        "crash_title": "autopilot: stopped",
        "crash_body": "Unexpected error. See the end of autopilot.log.",
        "day_0": "Mon", "day_1": "Tue", "day_2": "Wed", "day_3": "Thu", "day_4": "Fri", "day_5": "Sat", "day_6": "Sun",
    },
}


def language(lang):
    """`lang` when it is a known language; English otherwise (a missing, old or hand-edited value)."""
    return lang if isinstance(lang, str) and lang in TEXTS else DEFAULT


def text(lang, key, **fields):
    """The owner-facing text `key` in `lang`, with `{field}`s filled. Values are inserted as they are."""
    return TEXTS[language(lang)][key].format(**fields)


def language_name(lang):
    """The language as the prompts name it, e.g. "English (en)"."""
    return NAMES[language(lang)]


def day(lang, weekday):
    """Short day name, Monday = 0: "sex" in pt-BR, "Fri" in English."""
    return text(lang, f"day_{weekday}")
