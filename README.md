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
python3 -m venv .venv && .venv/bin/pip install -r hub/requirements.txt
cd hub && ../.venv/bin/python -m buddy_hub            # http://localhost:8765
# na rede local (para o ESP32): --host 0.0.0.0
```

O token é criado em `~/.config/claude-buddy/token` na primeira execução (ou use `BUDDY_TOKEN`). Abra `http://localhost:8765/` e cole o token no painel.

### Ligando nas sessões do Claude Code

1. Clone este repo em `~/ClaudeBuddy` no Ubuntu.
2. Mescle `hooks/settings.example.json` em `~/.claude/settings.json`.
3. Rode as sessões dentro do tmux para o ditado funcionar: `tmux new -s portrait-api claude`.

O nome da sessão no Buddy é o nome da sessão tmux (ou a pasta, fora do tmux).

## Comportamento das aprovações

- O hook `PermissionRequest` só espera o Buddy **se houver um Buddy conectado**. Sem Buddy, devolve na hora e o diálogo normal aparece.
- Com Buddy: espera até `BUDDY_APPROVAL_TIMEOUT` (padrão 20 s). Sem toque, cai para o diálogo normal (terminal / Remote Control).
- Comandos perigosos (`rm -rf`, `git push --force`, `reset --hard`, `sudo`, deploy…) aparecem marcados e **não podem ser aprovados por voz**, só por toque.
- Em modo `bypassPermissions` não há pedidos de permissão, então o Buddy só mostra status.

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
- [ ] STT no servidor (faster-whisper; GPU do desktop quando disponível)
- [ ] Firmware ESP32-C5 (LVGL + LovyanGFX), testado no Wokwi
- [ ] Carcaça impressa (Bambu A1)
- [ ] Agente falante (TTS)
