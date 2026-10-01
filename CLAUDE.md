# ClaudeBuddy: contexto do projeto

Projeto pessoal do Michael (não é da Portrait). Responda em português.
Commits com a identidade pessoal: `Michael Stumpf Sampaio <michstumpf@gmail.com>` (já configurada no repo; confira com `git config user.email` após clonar e ajuste se precisar).

## O que é

Mascote de mesa numa **ESP32-C5 NM-CYD-C5** (tela 2.8" touch, WiFi 2.4/5 GHz, BLE, 802.15.4) que:
- mostra o estado das sessões do Claude Code (rostos: trabalhando, esperando, terminou, dormindo, offline);
- aprova/nega `PermissionRequest` com um toque;
- recebe ditado por voz e entrega na sessão certa;
- (futuro) responde falando.

O hardware ainda não chegou. Até lá: simulador no navegador (`simulator/index.html`) e, depois, firmware no Wokwi (ESP32-C5 é alpha no Wokwi; se falhar, simular com ESP32-S3).
Confirmar quando chegar: controlador da tela (ILI9341/ST7789), tipo de touch, PSRAM, mic/speaker (senão: INMP441 + MAX98357A), se o firmware de fábrica é xiaozhi.

## Máquinas do Michael

| Máquina | Papel |
|---|---|
| **Notebook Ubuntu** | Trabalho pesado com Claude Code (Portrait). **Hub mora aqui.** Sessões em tmux nomeado com Remote Control. |
| Notebook Windows | Unidade móvel (sofá). Remote Control, Jira, Git. Futuro atalho de ditado → hub. |
| Desktop Windows RTX 3070 Ti | Jogos; opcional como servidor de STT na GPU (faster-whisper large-v3-turbo), com fallback para CPU no Ubuntu. |

Rede entre elas: Tailscale (planejado). Áudio/transcrição nunca saem de casa; contexto da Portrait não sai do Ubuntu.

## Decisões de arquitetura

- **Fonte da verdade: sessões em tmux nomeado no Ubuntu.** Terminal, Remote Control e Buddy são só janelas para a mesma sessão.
- **Entrada (ditado): `tmux send-keys -l`**, entra como se o usuário digitasse. Evita o problema do Wayland (sem xdotool/ydotool).
- **Saída (status): hooks do Claude Code** → `hooks/buddy_hook.py` → `POST /hook`. `Stop` traz `last_assistant_message` (não precisa ler o transcript).
- **Aprovação: hook `PermissionRequest`** (só dispara quando um diálogo apareceria, ao contrário de `PreToolUse`). Saída: `{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow|deny"}}}`; saída vazia = diálogo normal.
  - Sem Buddy conectado → responde na hora (não atrasa nada).
  - Com Buddy → espera `BUDDY_APPROVAL_TIMEOUT` (20 s), depois cai para o diálogo normal.
  - Comandos perigosos só aprovam por toque, nunca por voz.
- **Mensagens entre sessões (SendMessage) NÃO são base do projeto.** Testado em 2026-10-01: a entrega funciona e chega rotulada como "outra sessão", mas a sessão de destino não agiu sozinha e, quando instruída, não encontrou a mensagem nas próprias ferramentas. Serve no máximo como extra para avisos.
- **Fase 3 (agente falante):** recepcionista rápido (Haiku) com ferramentas sobre as sessões; STT faster-whisper com prompt inicial de jargão técnico; TTS Piper.

## Estado atual

Feito (fases 0–2):
- `hub/` FastAPI: estado das sessões, broker de aprovações, ditado (tmux ou dry-run), WebSocket do dispositivo. Protocolo no README.
- `hooks/buddy_hook.py` (stdlib, no-op se o hub cair) + `hooks/settings.example.json`.
- `simulator/index.html`: tela 320×240, rostos, lista, aprovação, ditado via Web Speech API, painel de eventos falsos.
  - O rosto é um sprite em pixel art do mascote do Claude Code, refeito a partir do logo do terminal (`BODY`/`EYES` no JS; 1 unidade = 10 px). **Uso pessoal apenas:** trocar por um personagem próprio antes de publicar o repo ou a carcaça. A grade de pixels porta direto para o firmware (LVGL canvas).
  - Na lista há um botão "◀ Buddy"; lista e detalhe voltam sozinhos para o rosto após 30 s sem toque.
- `hub/buddy_hub/local_sessions.py`: lê `~/.claude/sessions/<pid>.json` (nome do `/rename`, busy/idle/waiting, pane tmux) e corrige o que os hooks não reportam. Detalhes no README.
- `hub/buddy_hub/stt.py` + `POST /api/transcribe`: faster-whisper `small` em CPU, carregado na primeira chamada. `av<16` fixado (o faster-whisper 1.2.x quebra com PyAV 16+).
- `ptt/buddy_ptt.py`: F9 no Ubuntu (X11), tocar e só falar (para no silêncio) ou segurar → `pw-record` → hub → digita na janela em foco + Enter. `pynput` instalado com `--no-deps` (o `evdev` só serve para Wayland e precisa de `python3-dev`).
- `deploy/systemd/`: `claude-buddy-hub` (sobe no login) e `claude-buddy-ptt` (sobe com a sessão gráfica). Substituem o hub em tmux.
- Simulador: o rótulo de "trabalhando" imita o spinner do Claude Code (`· ✢ ✳ ✶ ✻ ✽` + verbos como "Accomplishing…"); ao terminar mostra "✻ Brewed for 1m 3s · <sessão>".
- `tests/`: 31 testes passando (`pytest -q tests`).

**Validado no Ubuntu em 2026-10-01** com uma sessão real em tmux: status, ditado via `tmux send-keys`, aprovar e negar (pelo Buddy falso via WebSocket e pelo simulador no Chrome), timeout caindo para o diálogo normal.

Aprendizados da validação:
- Esc no meio de uma resposta não dispara `Stop`; negar no terminal não dispara `PostToolUse`. O registro local cobre os dois.
- Sessões já abertas recarregam os hooks quando o `settings.json` muda.
- O modo padrão do Claude Code agora é auto mode; aprovação pelo Buddy só faz sentido em manual mode.
- O backend de ditado é escolhido quando o hub sobe: instalar o tmux depois exige reiniciar o hub.
- No Ubuntu o hub roda como serviço systemd de usuário (`deploy/systemd/`); logs em `journalctl --user -u claude-buddy-hub -f`.

## Próximos passos (em ordem)

1. ~~No Ubuntu: validar hub + hooks + tmux com uma sessão real~~ (feito em 2026-10-01; clone em `~/personal/ClaudeBuddy`).
2. Atalho de ditado no notebook Windows (segurar tecla → mic → STT → `POST /api/dictate` no hub via Tailscale).
3. STT na GPU do desktop: worker faster-whisper (CUDA, `large-v3-turbo`) no Windows via Tailscale; o hub tenta a GPU primeiro e cai para a CPU local se o desktop estiver desligado, ocupado ou lento. O ponto de troca é a classe `Transcriber` em `stt.py`. (STT em CPU no hub + F9 no Ubuntu: feito em 2026-10-01.)
4. Firmware ESP32-C5: ESP-IDF 5.5+ ou Arduino core 3.3+, LVGL + LovyanGFX, mesmo protocolo do simulador; testar no Wokwi (extensão do VS Code para alcançar o hub local).
5. Carcaça na Bambu A1 (estilo TV retrô com antena + LED; OpenSCAD paramétrico; mic na frente, speaker em câmara separada; PLA/PETG; sem logo da Anthropic se publicar).
6. Agente falante.

## Rodando

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt   # ou uv, se faltar python3-venv
.venv/bin/python -m pytest -q tests
cd hub && ../.venv/bin/python -m buddy_hub            # --host 0.0.0.0 para o ESP32 na LAN
```
Token: `~/.config/claude-buddy/token` (ou `BUDDY_TOKEN`). Simulador: `http://localhost:8765/`.
