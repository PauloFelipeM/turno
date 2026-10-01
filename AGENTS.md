# codex-autopilot

Agendador que roda o Codex sem supervisão em três projetos que dividem a mesma cota da conta do
ChatGPT. Esta pasta é a ferramenta em si; os projetos ficam em outros lugares. Aqui você mantém o
agendador, e não trabalha nos projetos.

Código, identificadores e mensagens de commit em inglês; comentários, docstrings, logs e
documentação em português (pt-BR), como no resto do arquivo.

## Regra número um

**Nunca rode `autopilot.py run` a partir de uma sessão do Codex.** O loop dispara outras sessões do
Codex, gasta a mesma cota que você e morre junto com a sua sessão. Quem inicia o loop é o Paulo, num
tmux (ver `README.md`). Os comandos `status`, `decisions` e `test-notify` são seguros; `answer` e
`reset` alteram estado e só rodam quando o Paulo pedir.

## Arquivos

| Caminho | O que é |
|---|---|
| `autopilot.py` | O agendador inteiro (Python 3.12, só biblioteca padrão) |
| `projects.toml` | Projetos em ordem de prioridade, tetos de cota, modelos e ntfy |
| `prompts/unit.md` | Prompt de toda unidade nova: o protocolo que o orquestrador segue |
| `prompts/resume.md` | Prompt para retomar uma unidade interrompida |
| `state.json` | Estado por projeto (gerado; não edite com o loop rodando) |
| `autopilot.log` | Log do loop (gerado) |
| `runs/` | JSONL e stderr de cada execução do `codex exec`, as 80 mais recentes (gerado) |
| `~/.codex/agents/*.toml` | Papéis globais: `planner`, `plan-reviewer`, `implementer`, `reviewer` |
| `<projeto>/docs/autopilot.md` | Regras de cada projeto: backlog, caminhos, verificação, git |

Os três projetos, em ordem de prioridade:

| Projeto | Caminho | Git | Teto semanal |
|---|---|---|---|
| my-app | `~/projects/my-app` | `main`, sem remote | 95% |
| my-api | `~/projects/my-api` | `main`, sem remote | 85% |
| side-project | `~/projects/side-project` | `feat/`/`fix/` → merge na `master` → push | 60% |

O my-app e o side-project usam as mesmas portas no Docker (8000, 3306, 5173): `docker_group = "web"`.

## Como funciona

Uma **unidade** é um passo do ciclo spec → plano → execução: aplicar respostas de decisões,
recuperar um task interrompido, executar um task, finalizar um item, escrever um plano, escrever uma
spec ou propor o backlog. Cada unidade é uma sessão curta do `codex exec`, e o orquestrador termina
escrevendo `<projeto>/.superpowers/autopilot/last-unit.json` com o status `worked`, `waiting`,
`idle` ou `blocked`.

A cada volta, o `cmd_run`:

1. `process_decisions`: envia decisões novas ao ntfy, lê as respostas e aplica o padrão quando o
   prazo vence;
2. `current_limits`: lê o uso das janelas de 5 h e semanal do log de sessão mais recente em
   `~/.codex/sessions` (campo `rate_limits`), sem chamar o modelo;
3. `pick`: a unidade interrompida vai primeiro, sempre. Senão, o primeiro projeto por prioridade
   que não esteja `idle`, `waiting` ou em cooldown de `blocked`, que tenha a árvore limpa e cujo uso
   semanal mais `safety_factor` × o custo aprendido caiba no `weekly_cap`;
4. confere a folga na janela de 5 h;
5. `prepare_docker`: garante o Docker Desktop, para o stack do outro projeto do grupo (salvo se esse
   projeto tiver trabalho sem commit) e sobe o do projeto com `up -d --wait`;
6. `run_unit` e depois `after_unit`: classifica a saída (`usage_limit`, `error`, `ok`), aprende o
   custo da unidade (média móvel exponencial) e atualiza o estado;
7. `nap`: dorme em fatias de 1 min, lendo respostas do ntfy.

Invariantes que uma mudança não pode quebrar:

- **Nada fica pela metade.** `interrupted` só volta a `False` quando a unidade fecha com
  `last-unit.json`. Uma unidade interrompida é retomada antes de qualquer outra, até acima do teto.
- **Trabalho de outra pessoa não é tocado.** Projeto com árvore suja e sem unidade interrompida é
  pulado, e o stack Docker dele não é parado.
- **Custo medido uma vez por unidade**, só quando ela fecha: uma retomada não conta duas vezes.
- **Respostas do ntfy só para decisão já enviada, sem resposta, e opção listada.** Valem os botões
  (`projeto/decisão/opção` no tópico `-reply`) e a letra digitada no app, no tópico principal
  (`B`, `B, texto…`, `my-app B`; o projeto é obrigatório com mais de uma pendente). Mensagem com
  título é do próprio agendador e é ignorada. Nenhum outro texto vale: o tópico é público para
  quem souber o nome. Resposta que não se encaixa recebe uma notificação explicando o formato.
- **Falha de rede ou de Docker nunca derruba o loop.** Registra, avisa uma vez e tenta de novo.

## Decisões pelo celular

O orquestrador grava `<projeto>/.superpowers/autopilot/decisions/<id>.json`, com 2 ou 3 opções e um
`default`, e termina a unidade como `waiting`. O agendador acrescenta `notified_at` e `deadline`, e
depois `answer`, `answered_by` (`owner`, `timeout` ou `invalid-format`) e `answered_at`. A unidade
seguinte aplica a resposta e grava `"applied": true`. O formato completo está no passo 6 de
`prompts/unit.md`, e a validação em `valid_decision`.

O `topic` em `projects.toml` é segredo: não o imprima em logs, commits ou mensagens.

## Ao mudar algo

- **Mudou o protocolo** (`prompts/`, papéis, `last-unit.json`, arquivos de decisão): mantenha
  coerentes `prompts/unit.md`, os papéis em `~/.codex/agents/` e o `docs/autopilot.md` de cada
  projeto.
- **Não há suíte de testes automatizada.** Teste importando o módulo e substituindo os pontos de
  efeito colateral: `ap.STATE` e `ap.LOG` em pasta temporária, `ap.ntfy`, `ap.is_dirty`,
  `ap.current_limits`, `ap.unit_cost`, `ap.compose`, `ap.docker_up`, `ap.time.sleep`. Exemplo:

  ```python
  import importlib.util, tempfile
  from pathlib import Path
  spec = importlib.util.spec_from_file_location("ap", "autopilot.py")
  ap = importlib.util.module_from_spec(spec); spec.loader.exec_module(ap)
  tmp = Path(tempfile.mkdtemp()); ap.STATE = tmp / "state.json"; ap.LOG = tmp / "log"
  ap.ntfy = lambda *a, **k: None
  ```

  Nunca teste contra o `state.json` real, nem chamando `codex exec`, nem parando containers dos
  projetos.
- Depois de editar: `python3 -m py_compile autopilot.py` e `python3 autopilot.py status`.
- Se o loop estiver rodando, a mudança só vale depois que o Paulo reiniciar o loop. Diga isso a ele.
- Esta pasta não está sob git. Antes de uma mudança grande, sugira `git init` ao Paulo.
