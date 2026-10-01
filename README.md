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
- Configuração: `BUDDY_PTT_KEY` (padrão `f9`), `BUDDY_PTT_ENTER=0` para só digitar sem Enter, `BUDDY_STT_MODEL` no hub (padrão `small`; `large-v3-turbo` acerta mais jargão, mas é bem mais lento em CPU), `BUDDY_STT_LANGUAGE` (padrão `pt`; vazio = detectar), `BUDDY_STT_BEAM_SIZE` (padrão `1`, o mais rápido; `5` acerta um pouco mais, mas é 2-3× mais lento em CPU).
- O modelo carrega na primeira transcrição (~600 MB de RAM no hub) e fica em memória.
- Apertar por menos de 0,4 s conta como toque; frases que o Whisper inventa no silêncio ("Legendas pela comunidade Amara.org") são descartadas.

## Resposta falada

Quando uma pergunta é **feita por voz** (F9 ou ditado pelo Buddy), o hub fala a resposta da sessão quando ela termina. Perguntas digitadas continuam em silêncio, então as outras sessões não viram narração.

- **Como o hub sabe:** guarda o texto de cada transcrição por 60 s; o `UserPromptSubmit` que chega com esse texto (ou começando por ele) marca a sessão, e o `Stop` dela é falado.
- **O que é falado:** um resumo de 1-2 frases escrito pelo **Claude Haiku** (`summarizer.py`), com a pergunta como contexto. Chave em `~/.config/claude-buddy/anthropic_key` (ou `BUDDY_ANTHROPIC_API_KEY`) — use a chave **aprovada pela Portrait**, porque perguntas por voz em sessões da Portrait mandam a resposta para ela. Sem chave, ou se o Haiku falhar ou demorar mais de 6 s, fala as primeiras 1-3 frases da resposta sem código, tabelas, links, emojis ou markdown (`speakable()` em `tts.py`).
- **Modo noite:** dizer "boa noite" (ou "vou dormir", "encerrando por hoje") por voz, numa frase curta, faz o Buddy dormir: rosto dormindo, sem bipes, sem falar. Pedidos de aprovação ainda aparecem, sem bipe. Acorda com "bom dia", com qualquer outro ditado ou com um toque na tela.
- **Voz:** **XTTS-v2 na GPU do desktop** (o mesmo worker da transcrição, rota `/speak`; voz `BUDDY_XTTS_SPEAKER`, padrão "Gilberto Mathias", ou `BUDDY_XTTS_SPEAKER_WAV` para clonar uma voz de um WAV de 6-30 s). Se o desktop estiver indisponível, ocupado ou sem a voz instalada, o hub fala com o **Piper** local (~0,15 s por frase; vozes pt-BR `faber` (padrão), `cadu`, `jeff` em `BUDDY_TTS_VOICE`). A licença do modelo XTTS-v2 é só para uso não comercial.
- **Onde toca** (`BUDDY_SPEAK_ON`): `auto` (padrão) toca no Buddy/simulador se houver um conectado, senão nas caixas do hub (`pw-play`); também `local`, `devices`, `off`. O Buddy recebe `{"type":"speech","id","session","text","url"}` e busca o WAV em `GET /api/speech/<id>?token=…`.

```bash
uv pip install --python .venv/bin/python -r hub/requirements-tts.txt
mkdir -p ~/.local/share/claude-buddy/voices && cd ~/.local/share/claude-buddy/voices
~/personal/ClaudeBuddy/.venv/bin/python -m piper.download_voices pt_BR-faber-medium
```

## Transcrição na GPU do desktop (opcional)

O hub manda o áudio primeiro para um worker na GPU do desktop Windows (`buddy_hub.worker`, faster-whisper `large-v3-turbo` em CUDA) e transcreve na própria CPU se o desktop estiver desligado, demorar mais de 1 s para responder ao `/health`, estiver com a GPU ocupada (≥ 60% de uso ou menos de 2,5 GB de VRAM livre, por exemplo num jogo) ou falhar. O modelo local só carrega quando o fallback acontece.

```
F9 / Buddy ──áudio──▶ hub (Ubuntu) ──Tailscale──▶ worker (desktop, GPU) ──texto──▶ hub ──▶ digitado no Ubuntu
                         └── desktop indisponível: CPU do hub
```

### 1. Tailscale nas duas máquinas

- **Ubuntu:** `curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`
- **Desktop:** instale pelo site (tailscale.com/download) e entre na mesma conta.
- Confira no Ubuntu: `tailscale status` mostra o desktop pelo nome (MagicDNS) e `ping <nome-do-desktop>` responde.

### 2. Desktop Windows

1. Driver NVIDIA atualizado (o CUDA Toolkit **não** é necessário; o runtime vem pelo pip).
2. `winget install Python.Python.3.12 Git.Git`
3. Clone o repo (privado; o Git for Windows abre o login do GitHub no navegador): `git clone https://github.com/Michstumpf/ClaudeBuddy.git`
4. Num PowerShell **como administrador**, na pasta do repo:
   ```powershell
   powershell -ExecutionPolicy Bypass -File deploy\windows\install-worker.ps1
   ```
   O script cria o venv, instala as dependências (incluindo a voz XTTS-v2: PyTorch com CUDA + coqui-tts, ~3 GB; `-NoVoice` pula), pede o token do hub (`cat ~/.config/claude-buddy/token` no Ubuntu), testa a GPU (baixa ~1,6 GB na primeira vez), libera a porta 8766 no firewall **só para a faixa do Tailscale** e agenda o worker para subir no login, escondido. Log em `%LOCALAPPDATA%\ClaudeBuddy\worker.log`. Pode rodar de novo depois de um `git pull`.

### 3. Ubuntu: apontar o hub para o desktop

```bash
systemctl --user edit claude-buddy-hub      # adicione as duas linhas abaixo
#   [Service]
#   Environment=BUDDY_STT_REMOTE=http://<nome-do-desktop>:8766
systemctl --user restart claude-buddy-hub
curl -s http://<nome-do-desktop>:8766/health -H "X-Buddy-Token: $(cat ~/.config/claude-buddy/token)"
```

Cada transcrição registra no log do hub onde rodou: `on remote:cuda` (GPU) ou `on cpu` (fallback, com o motivo).

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

Hub → Buddy também: `{"type":"speech","id":"…","session":"…","text":"…","url":"/api/speech/…"}` (resposta falada; o WAV pede `?token=`).

O firmware do ESP32 vai falar exatamente esse protocolo; o simulador é a referência.

## Roadmap

- [x] Fase 0–2: hub, hooks, aprovação, ditado via tmux, simulador
- [ ] Atalho de ditado no notebook Windows → hub
- [x] STT no hub (faster-whisper em CPU) + segurar F9 no Ubuntu
- [x] Worker de STT na GPU do desktop + fallback para a CPU do hub
- [ ] Firmware ESP32-C5 (LVGL + LovyanGFX), testado no Wokwi
- [ ] Carcaça impressa (Bambu A1)
- [x] Resposta falada a perguntas por voz (Piper local)
- [ ] Resumo falado com o Claude Haiku; voz mais natural na GPU (XTTS/Kokoro)
