# ClaudeBuddy

Um mascote de mesa (ESP32-C5 NM-CYD-C5, tela 2.8" touch) que mostra o que as sessões do Claude Code estão fazendo, deixa aprovar permissões com um toque e recebe ditado por voz.

```
 Buddy (ESP32 / simulador) ◀──WebSocket──▶ buddy-hub (Ubuntu) ◀──HTTP── hooks do Claude Code
                                               │
                                               └── tmux send-keys ──▶ sessões em tmux
```

## Peças

| Pasta | O que é |
|---|---|
| `hub/` | Servidor (FastAPI). Recebe hooks, mantém o estado das sessões, segura pedidos de permissão esperando o Buddy, entrega ditado via tmux. |
| `hooks/buddy_hook.py` | Hook do Claude Code. Só stdlib. Se o hub estiver fora do ar, não faz nada e o Claude Code segue normal. |
| `simulator/` | O Buddy no navegador, com a tela 320×240 exata, para desenvolver antes do hardware. Ditado via Web Speech API (Chrome/Edge). |
| `tests/` | `pytest` |

## Rodando

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
# Ubuntu sem python3-venv: uv venv .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest -q tests
cd hub && ../.venv/bin/python -m buddy_hub            # http://localhost:8765
# na rede local (para o ESP32): --host 0.0.0.0
```

O token é criado em `~/.config/claude-buddy/token` na primeira execução (ou use `BUDDY_TOKEN`). Abra `http://localhost:8765/` e cole o token no painel.

### Ligando nas sessões do Claude Code

1. Clone este repo no Ubuntu (o exemplo usa `~/ClaudeBuddy`; ajuste o caminho se clonar em outro lugar).
2. Mescle `hooks/settings.example.json` em `~/.claude/settings.json`. Sessões já abertas recarregam os hooks sozinhas.
3. Rode as sessões dentro do tmux para o ditado funcionar: `tmux new -s portrait-api claude`.

Nome da sessão no Buddy, em ordem de prioridade: o nome dado com `/rename`, o nome da sessão tmux, o nome que o Claude Code gera (ex.: `git-d6`), a pasta.

### Registro local de sessões

O hub também lê `~/.claude/sessions/<pid>.json`, que o Claude Code mantém para cada processo aberto (a cada 3 s; `BUDDY_SESSIONS_DIR=""` desliga). Com isso:

- sessões que ainda não dispararam nenhum hook aparecem mesmo assim;
- o status se corrige onde os hooks são cegos: Esc no meio de uma resposta não dispara `Stop`, e negar um diálogo no terminal não dispara `PostToolUse`;
- diálogos de permissão abertos no terminal aparecem como `waiting`;
- processos que morreram sem `SessionEnd` viram `offline`.

Sessões que só chegam por hook (outras máquinas, eventos falsos do simulador) não têm esse registro: se ficarem `working` por 10 min sem nenhum evento (`BUDDY_STALE_WORKING`), voltam para `idle`.

O arquivo só vence quando é mais novo que o último hook, então nunca desfaz um evento que acabou de chegar. Só vale para sessões na mesma máquina do hub.

## Ditado por voz no Ubuntu (segurar F9)

`ptt/buddy_ptt.py` (sessão X11), dois jeitos:

- **toque** no F9 e só fale: envia sozinho depois de 1,5 s de silêncio (`BUDDY_PTT_SILENCE`), ou num segundo toque; sem fala nenhuma em 8 s, cancela;
- **segure** o F9 enquanto fala e solte para enviar.

O áudio é gravado com `pw-record`, transcrito pelo hub (`POST /api/transcribe`, faster-whisper em CPU, nada sai de casa) e digitado na janela em foco, seguido de Enter. Funciona em qualquer terminal ou app, com ou sem tmux.

```bash
uv pip install --python .venv/bin/python -r hub/requirements-stt.txt python-xlib six
uv pip install --python .venv/bin/python --no-deps pynput   # o evdev só serve para Wayland e exige python3-dev
cp deploy/systemd/*.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now claude-buddy-hub claude-buddy-ptt
journalctl --user -u claude-buddy-ptt -f
```

- O hub sobe sozinho no login (`claude-buddy-hub.service`); o PTT sobe com a sessão gráfica (`claude-buddy-ptt.service`). Os caminhos assumem o clone em `~/personal/ClaudeBuddy`.
- Configuração: `BUDDY_PTT_KEY` (padrão `f9`), `BUDDY_PTT_ENTER=0` para só digitar sem Enter, `BUDDY_STT_MODEL` no hub (padrão `small`; `large-v3-turbo` acerta mais jargão, mas é bem mais lento em CPU), `BUDDY_STT_LANGUAGE` (padrão `pt`; vazio = detectar).
- O modelo carrega na primeira transcrição (~600 MB de RAM no hub) e fica em memória.
- Apertar por menos de 0,4 s conta como toque; frases que o Whisper inventa no silêncio ("Legendas pela comunidade Amara.org") são descartadas.

## Comportamento das aprovações

- O hook `PermissionRequest` só espera o Buddy **se houver um Buddy conectado**. Sem Buddy, devolve na hora e o diálogo normal aparece.
- Com Buddy: espera até `BUDDY_APPROVAL_TIMEOUT` (padrão 20 s). Sem toque, cai para o diálogo normal (terminal / Remote Control).
- Comandos perigosos (`rm -rf`, `git push --force`, `reset --hard`, `sudo`, deploy…) aparecem marcados e **não podem ser aprovados por voz**, só por toque.
- Em modo `bypassPermissions` não há pedidos de permissão, então o Buddy só mostra status.
- O modo padrão do Claude Code agora é **auto mode**, que decide sozinho a maioria das permissões. Para aprovar pelo Buddy, use manual mode (shift+tab).

## Protocolo do dispositivo (WebSocket `/ws?token=…`)

Hub → Buddy: `{"type":"state","sessions":[…],"pending":[…],"devices":N,"event":{"kind":"done|attention|approval","session":"…"}}`

Buddy → hub:
- `{"type":"decision","id":"…","behavior":"allow|deny","via":"touch|voice"}`
- `{"type":"dictate","session_id":"…","text":"…"}`
- `{"type":"ping"}`

O firmware do ESP32 vai falar exatamente esse protocolo; o simulador é a referência.

## Roadmap

- [x] Fase 0–2: hub, hooks, aprovação, ditado via tmux, simulador
- [ ] Atalho de ditado no notebook Windows → hub
- [x] STT no hub (faster-whisper em CPU) + segurar F9 no Ubuntu
- [ ] STT na GPU do desktop Windows, com fallback para a CPU do hub
- [ ] Firmware ESP32-C5 (LVGL + LovyanGFX), testado no Wokwi
- [ ] Carcaça impressa (Bambu A1)
- [ ] Agente falante (TTS)
