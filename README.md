# codex-autopilot

Roda o Codex sozinho no my-app, no my-api e no side-project, nessa ordem de prioridade. Ele divide a
cota da conta entre os três, espera o reset quando ela acaba e te pergunta pelo celular (ntfy) as
decisões de produto que importam.

Uma segunda instância roda o Claude Code nos mesmos projetos, só nos fins de semana (ver "Claude
nos fins de semana"). As duas revezam: nunca trabalham no mesmo projeto nem no mesmo grupo Docker
ao mesmo tempo.

## Iniciar

Na primeira vez, crie a configuração a partir dos exemplos e troque o `topic` do ntfy, no
`secrets.toml`, por um nome aleatório só seu (o `secrets.toml` fica fora do git porque guarda esse
segredo):

```bash
cd ~/.turno
cp projects.example.toml projects.toml
cp secrets.example.toml secrets.toml
```

1. Confira o estado:
   ```bash
   python3 ~/.turno/autopilot.py status
   ```
2. Inicie dentro do tmux:
   ```bash
   tmux new -s autopilot 'python3 ~/.turno/autopilot.py run'
   ```
3. Solte o terminal com `Ctrl+B` e depois `D`. Pode fechar o terminal e o VS Code.

O Docker não precisa estar no ar: o agendador abre o Docker Desktop e sobe os containers de cada
projeto antes de cada unidade.

## Comandos

Todos começam com `python3 ~/.turno/autopilot.py`. Para encurtar, você pode criar um
alias no `~/.bashrc`:

```bash
alias autopilot='python3 ~/.turno/autopilot.py'
```

Os exemplos abaixo usam o alias. `autopilot --help` lista todos os comandos.

### `autopilot run`: iniciar o loop

Roda o agendador até todos os projetos terminarem o backlog. Use sempre dentro do tmux (ver
"Iniciar"); fora dele, o loop para quando você fecha o terminal. Se já tiver rodado antes, ele
retoma de onde parou, começando pela unidade que estava no meio.

### `autopilot status`: ver como estão as coisas

Mostra o uso das duas janelas de cota e o estado de cada projeto. Não altera nada; pode rodar a
qualquer hora, com o loop rodando ou não.

```
janela 5 h       54.0%   reset 2026-09-28 14:43:29
janela semanal   11.0%   reset 2026-10-04 23:51:09

my-app    active   teto  95%  custo/unidade 8.0% (5 h) 1.50% (semana)  árvore suja
my-api     active   teto  85%  custo/unidade 8.0% (5 h) 1.50% (semana)
side-project     waiting  teto  60%  custo/unidade 6.2% (5 h) 1.10% (semana)

1 decisão(ões) pendente(s): autopilot.py decisions
```

Os estados possíveis de um projeto:

| Estado | Significa |
|---|---|
| `active` | Na fila; roda quando for a vez dele e houver cota |
| `waiting` | Esperando a sua resposta a uma decisão (até o prazo) |
| `blocked` | Travou em algo que não resolve sozinho; tenta de novo em 6 h |
| `idle` | Terminou o backlog; não roda mais até um `reset` |

E as marcas no fim da linha:

| Marca | Significa |
|---|---|
| `árvore suja` | Há alterações sem commit que não são do autopilot; o projeto fica de fora |
| `unidade interrompida` | Uma unidade parou no meio; ela é a próxima a rodar |
| `sessão <id>` | A sessão do Codex (ou do Claude) que vai ser retomada |
| `reservado: <motor>` | O projeto tem uma unidade aberta nesse motor; o outro não mexe nele |
| `bastão de <motor>` | Uma unidade foi cortada no fim da janela; o próximo motor a rodar retoma |

O custo por unidade começa em 8% (5 h) e 1,5% (semana) e se ajusta ao custo real a cada unidade.

### `autopilot decisions`: ver as decisões pendentes

Lista as perguntas que esperam resposta, com o prazo, a opção padrão e as opções.

```
side-project / stock-negative   prazo 2026-09-28 18:02:11   padrão a
  Permitir estoque negativo em transferências entre lojas?
    a) Não permitir
    b) Permitir com alerta
```

### Responder pelo celular

Toque no botão da opção na notificação (no Android, puxe a notificação para baixo para ver os
botões) ou digite a letra no campo de mensagem do tópico, no app do ntfy:

| Você digita | Vale quando |
|---|---|
| `B` ou `B, manter até trocar a senha` | Há uma só pergunta aberta |
| `my-app B` | Há perguntas abertas em mais de um projeto |

O que vale é a letra no início. Texto sem a letra, como "Email cadastrado", não é aceito, e o
autopilot responde avisando o formato. Quando ele aceita, chega uma notificação "anotado".

### `autopilot answer <projeto> <id> <opção>`: responder pelo terminal

Faz o mesmo que tocar no botão do celular. Use o `id` e a letra que o `decisions` mostra:

```bash
autopilot answer side-project stock-negative b
```

A resposta vale na próxima volta do loop: o projeto é liberado e a próxima unidade dele aplica a
sua escolha. Não funciona para decisão já respondida, nem com opção fora da lista.

### `autopilot reset <projeto>`: reativar um projeto

Volta um projeto `idle`, `blocked` ou `waiting` para `active` e zera o contador de erros. Use
depois de resolver o que bloqueou o projeto, ou quando acrescentar itens ao backlog de um projeto
que tinha terminado.

```bash
autopilot reset my-api
```

Com o loop rodando, o `reset` pode ser sobrescrito na volta seguinte. É mais seguro parar o loop,
rodar o `reset` e iniciar de novo.

### `autopilot test-notify`: testar as notificações

Manda uma notificação de teste para o celular. Use se você trocou de celular, reinstalou o app ou
suspeita que as notificações pararam.

### Acompanhar e parar

| Para | Comando |
|---|---|
| Acompanhar o log ao vivo | `tail -f ~/.turno/autopilot.log` (`Ctrl+C` sai só do `tail`) |
| Voltar à tela do loop | `tmux attach -t autopilot` |
| Soltar a tela sem parar | `Ctrl+B`, depois `D` |
| Parar o loop | `tmux attach -t autopilot`, depois `Ctrl+C` |
| Ver se o loop está rodando | `tmux ls` (aparece `autopilot` na lista) |
| O mesmo para o Claude | troque `autopilot` por `autopilot-claude` e o log por `autopilot-claude.log` |

Parar é seguro a qualquer momento. Para retomar, rode de novo o comando do passo 2 de "Iniciar": ele
continua a unidade que estava no meio.

## Claude nos fins de semana

A instância do Claude usa o `claude.toml` e trabalha só de **sexta 19:00 a domingo 03:00**, até
**90%** da cota semanal do Claude. Fora da janela ela não consulta nem gasta a cota, então você
pode deixar o tmux aberto a semana toda.

1. Antes de sexta 19:00, confira o que ela faria (lê a cota com uma consulta mínima ao Haiku):
   ```bash
   python3 ~/.turno/autopilot.py --config claude.toml check
   ```
2. Inicie num tmux próprio:
   ```bash
   tmux new -s autopilot-claude 'python3 ~/.turno/autopilot.py --config claude.toml run'
   ```

Como funciona:

- **Modelos:** o Fable planeja (`planner`, `plan-reviewer`) e o Opus 5.5 executa (`implementer`,
  `reviewer` e o orquestrador). Quando a cota do Fable acaba, o planejamento passa para o Opus até
  ela voltar. Os papéis ficam em `claude-agents.json`, só para estas sessões; o Claude Code que você
  usa na semana não os vê.
- **Teto:** uma unidade só começa se o uso semanal mais 1,5 vez o custo médio dela couber em 90%.
- **Fim da janela:** às 02:45 ele para de começar unidades novas. Às 03:00 em ponto a unidade em
  andamento é cortada, e o projeto fica com o **bastão**: o próximo autopilot a rodar (em geral o
  Codex) retoma pelo ledger, mesmo com alterações sem commit.
- **Avisos:** "Claude: começou o fim de semana" e "Claude: fim de semana encerrado", com o uso da
  cota e os projetos que ficaram com o bastão.
- **Decisões:** as duas instâncias usam o mesmo tópico do ntfy e se revezam para ler as respostas;
  cada pergunta chega uma vez só.

O Codex precisa estar com o código atual antes de o Claude começar; senão ele não respeita as
reservas. Os comandos `status`, `check`, `decisions` e os outros aceitam `--config claude.toml`.

## O que interrompe o loop

- **Suspensão do Windows:** pausa tudo. Para rodar a noite toda, deixe "Suspender" em "Nunca" quando
  estiver na tomada.
- **Reinício do Windows ou `wsl --shutdown`:** mata o processo. Rode o passo 2 de novo.
- **Projeto com alterações sem commit:** fica de fora até a árvore ficar limpa.

## Ajustes

Tudo em `projects.toml` (Codex) e `claude.toml` (Claude): a ordem e os tetos semanais dos projetos,
os modelos do orquestrador, o número de sub-agents, a janela do Claude e o prazo das decisões
(`decision_timeout_hours`). Os modelos dos sub-agents ficam em `~/.codex/agents/` (Codex) e em
`claude-agents.json` (Claude). Mudanças valem quando você reinicia o loop.

Onde procurar quando algo der errado: `autopilot.log` e os arquivos `.err` e `.jsonl` em `runs/`
(no Claude, `autopilot-claude.log` e `runs-claude/`).
