# codex-autopilot

Agendador que roda o Codex sem supervisão em três projetos que dividem a mesma cota da conta do
ChatGPT, e uma segunda instância que roda o Claude Code nos mesmos projetos nos fins de semana. Esta
pasta é a ferramenta em si; os projetos ficam em outros lugares. Aqui você mantém o agendador, e não
trabalha nos projetos.

Código, identificadores e mensagens de commit em inglês; comentários, docstrings, logs e
documentação em português (pt-BR), como no resto do arquivo.

## Regra número um

**Nunca rode `autopilot.py start`, `autopilot.py run` nem `autopilot.py --config claude.toml run` a
partir de uma sessão do Codex ou do Claude Code.** O loop dispara outras sessões, gasta a mesma cota que você e morre junto
com a sua sessão. Quem inicia os loops é o Paulo, com `autopilot.py start` (ver `README.md`). Os comandos
`status`, `decisions` e `test-notify` são seguros; `check` também, mas lê a cota (no Claude, com uma
consulta mínima ao Haiku). `stop`, `answer` e `reset` alteram estado e só rodam quando o Paulo pedir.
Cada motor tem uma trava de instância (`shared/instance-<motor>.lock`, com o pid): nunca rodam dois
loops do mesmo motor.

## Arquivos

| Caminho | O que é |
|---|---|
| `autopilot.py` | CLI, decisões e ntfy, Docker, escolha do projeto e o laço (`run_once`, `run_project`) |
| `engines.py` | Motores `CodexEngine` e `ClaudeEngine`: comando, leitura da cota, classificação da saída, custo |
| `coord.py` | Reservas, bastão e travas entre as instâncias, em `shared/` |
| `schedule.py` | Janela semanal (a do Claude: sexta 19:00 a domingo 03:00) |
| `projects.toml` | Instância do Codex: projetos em ordem de prioridade, tetos, modelos |
| `claude.toml` | Instância do Claude: `[instance]`, `[engine]`, `[schedule]`, teto de 90% |
| `secrets.toml` | Tópico do ntfy, lido pelas duas instâncias (fora do git; ver `secrets.example.toml`) |
| `prompts/unit.md` | Prompt de toda unidade nova: o protocolo que o orquestrador segue |
| `prompts/resume.md` | Prompt para retomar uma unidade interrompida |
| `prompts/engine-*.md` | Nota de motor, anexada ao fim do prompt (o que é próprio do Codex ou do Claude) |
| `claude-agents.json` | Papéis do Claude (`planner`, `plan-reviewer`, `implementer`, `reviewer`), passados com `--agents` |
| `state.json`, `state-claude.json` | Estado de cada instância (gerado; não edite com o loop rodando) |
| `autopilot.log`, `autopilot-claude.log` | Log de cada instância (gerado) |
| `runs/`, `runs-claude/` | JSONL e stderr das execuções mais recentes (gerado) |
| `shared/` | Reservas (`claims/`), travas (`*.lock`) e `notify.json` (gerado) |
| `tests/` | Testes: `python3 -m unittest discover -s tests -v` |
| `~/.codex/agents/*.toml` | Papéis do Codex: `planner`, `plan-reviewer`, `implementer`, `reviewer` |
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

Cada instância tem um motor (`[engine] kind`, `codex` por padrão). A cada volta, o `run_once`:

1. `process_decisions`: sob `shared/decisions.lock`, envia decisões novas ao ntfy, lê as respostas e
   aplica o padrão quando o prazo vence (a posição de leitura fica em `shared/notify.json`);
2. com `[schedule]`, fora da janela ou nos últimos `start_margin_minutes` dela, só dorme: nem a cota
   é consultada;
3. `engine.limits`: o uso das janelas de 5 h e semanal. Codex: o log de sessão mais recente em
   `~/.codex/sessions`. Claude: o evento `rate_limit_event` da última execução ou de uma consulta
   mínima ao Haiku (`--safe-mode`) se a leitura tiver mais de 30 min. Sem leitura, o Claude não
   começa nada;
4. `pick`: a unidade interrompida deste motor e o bastão de qualquer motor vão primeiro. Senão, o
   primeiro projeto por prioridade que não esteja `idle`, `waiting` ou em cooldown de `blocked`, não
   reservado pelo outro motor, com a árvore limpa e cujo uso mais `safety_factor` × o custo
   aprendido caiba no `weekly_cap` (semana) e no `primary_cap` (5 h). Um projeto que não cabe não
   segura a fila: o próximo, mais barato, pode rodar. Pega a trava do grupo
   (`shared/<docker_group ou nome>.lock`);
5. `run_project`: grava a reserva, `prepare_docker` (para o stack
   do outro projeto do grupo, salvo se ele tiver trabalho sem commit, e sobe o do projeto);
6. `run_unit` (grava o estado antes de iniciar a CLI; com janela, corta no fim dela) e `after_unit`:
   classifica a saída (`usage_limit`, `error`, `ok` ou corte), aprende o custo da unidade (média
   móvel exponencial), solta ou passa a reserva e atualiza o estado;
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
- **Nunca dois motores no mesmo projeto ou grupo Docker.** A reserva (`shared/claims/`) dura até a
  unidade fechar, inclusive interrompida; a trava do grupo vale enquanto a CLI roda.
- **Bastão vai primeiro.** Unidade cortada no fim da janela vira reserva `any`: qualquer motor a
  retoma antes de começar outra, mesmo com a árvore suja, numa sessão nova a partir do ledger.
- **O Claude só trabalha dentro da janela e com a cota lida.** Fora dela, nem consulta a cota.

## Decisões pelo celular

O orquestrador grava `<projeto>/.superpowers/autopilot/decisions/<id>.json`, com 2 ou 3 opções e um
`default`, e termina a unidade como `waiting`. O agendador acrescenta `notified_at` e `deadline`, e
depois `answer`, `answered_by` (`owner`, `timeout` ou `invalid-format`) e `answered_at`. A unidade
seguinte aplica a resposta e grava `"applied": true`. O formato completo está no passo 6 de
`prompts/unit.md`, e a validação em `valid_decision`.

O `topic` em `secrets.toml` é segredo: não o imprima em logs, commits ou mensagens.

## Ao mudar algo

- **Mudou o protocolo** (`prompts/`, papéis, `last-unit.json`, arquivos de decisão): mantenha
  coerentes `prompts/unit.md`, as notas de motor (`prompts/engine-*.md`), os papéis em
  `~/.codex/agents/` e em `claude-agents.json`, e o `docs/autopilot.md` de cada projeto.
- **Testes:** `python3 -m unittest discover -s tests -v`. O `tests/helpers.py` carrega o autopilot
  com estado, log, `shared/` e ntfy numa pasta temporária, e traz um `FakeEngine` e um repositório git
  de teste. Teste novo segue esse padrão. Nunca teste contra o `state.json` real, nem chamando
  `codex` ou `claude` de verdade, nem parando containers dos projetos.
- Depois de editar: os testes, `python3 -m py_compile autopilot.py coord.py engines.py schedule.py`,
  `python3 autopilot.py status` e `python3 autopilot.py --config claude.toml status`.
- Se o loop estiver rodando, a mudança só vale depois que o Paulo reiniciar o loop. Diga isso a ele.
- Esta pasta está sob git (`main`, sem remote). Mudança grande vai numa branch.
