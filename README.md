# codex-autopilot

Roda o Codex sozinho no my-app, no my-api e no side-project, nessa ordem de prioridade. Ele divide a
cota da conta entre os três, espera o reset quando ela acaba e te pergunta pelo celular (ntfy) as
decisões de produto que importam.

## Iniciar

Na primeira vez, crie a configuração a partir do exemplo e troque o `topic` do ntfy por um nome
aleatório só seu (o `projects.toml` fica fora do git porque guarda esse segredo):

```bash
cp ~/.turno/projects.example.toml ~/.turno/projects.toml
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
| `sessão <id>` | A sessão do Codex que vai ser retomada |

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

Parar é seguro a qualquer momento. Para retomar, rode de novo o comando do passo 2 de "Iniciar": ele
continua a unidade que estava no meio.

## O que interrompe o loop

- **Suspensão do Windows:** pausa tudo. Para rodar a noite toda, deixe "Suspender" em "Nunca" quando
  estiver na tomada.
- **Reinício do Windows ou `wsl --shutdown`:** mata o processo. Rode o passo 2 de novo.
- **Projeto com alterações sem commit:** fica de fora até a árvore ficar limpa.

## Ajustes

Tudo em `projects.toml`: a ordem e os tetos semanais dos projetos, os modelos do orquestrador, o
número de sub-agents e o prazo das decisões (`decision_timeout_hours`). Os modelos dos sub-agents
ficam em `~/.codex/agents/`. Mudanças valem quando você reinicia o loop.

Onde procurar quando algo der errado: `autopilot.log`, e os arquivos `.err` e `.jsonl` em `runs/`.
