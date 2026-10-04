# Firmware do Claude Buddy

C++ (Arduino core 3.x via pioarduino) + LVGL 9 + LovyanGFX + ArduinoJson. O simulador do navegador (`../simulator/index.html`) continua sendo a referência de comportamento e visual.

| Pasta | O que é |
|---|---|
| `src/app/` | Estado, protocolo do hub e regras do rosto. C++ puro, testado no host (`pio test -e native`). |
| `src/hal/` | Uma implementação por placa (`board_wokwi_s3.cpp`, `board_m5_cores3.cpp`) da interface em `board.h`: tela, toque, bateria e o que a placa tem (`kHasAudio`…). **Só aqui muda de uma placa para outra.** |
| `src/ui/` | As telas (LVGL), o sprite do mascote (`sprite.h`, mesma grade do simulador) e as fontes. |
| `src/net/` | Link com o hub. Por enquanto o link serial de teste; o WebSocket vem a seguir. |
| `tools/` | `screens.py` (todas as telas no Wokwi) e `make_fonts.sh` (fontes com acentos). |

## Placas

| Ambiente | Placa | Para quê |
|---|---|---|
| `m5_cores3` | **M5Stack CoreS3** (ESP32-S3, 2" 320×240 touch, microfones, alto-falante, bateria) | O Buddy de verdade. `pio run -e m5_cores3 -t upload` pelo USB-C. Usa a M5Unified (tela, toque, energia, áudio). |
| `wokwi_s3` | ESP32-S3 + ILI9341 320×240 no Wokwi | Placa de referência para testar as telas sem hardware; mesma resolução do CoreS3. |
| `native` | o próprio Ubuntu | Testes do protocolo e das regras do rosto. |

A NM-CYD-C5 pode voltar como um "Buddy de status" (sem áudio): basta um `board_*.cpp` novo.

## Testar sem hardware

```bash
pio test -e native                                  # protocolo e regras, no Ubuntu
pio run -e wokwi_s3                                 # compila para o ESP32-S3 do Wokwi
WOKWI_CLI_TOKEN=$(cat ~/.config/claude-buddy/wokwi_token) python3 tools/screens.py
# -> wokwi/screenshots/*.png e contact-sheet.png, uma captura por estado
```

`screens.py` roda **uma** simulação com 12 estados — rosto (sem conexão, tranquilo, trabalhando, terminou, piada, noite), aprovação, lista, sessão e configurações — e **confere o que o firmware manda ao hub**: a decisão ao tocar em Aprovar, o `touch` ao tocar no rosto e o `settings` ao mudar uma opção. Toques são simulados pelo comando de teste `{"type":"_tap","x":…,"y":…}` na serial, que passa pela LVGL como um toque real. O Wokwi simula a velocidade real do SPI, então um redesenho de tela inteira leva um tempo: as capturas esperam ~1,5 s depois de trocar de tela.

`screens.py` roda **uma** simulação (poupa os minutos do plano gratuito do Wokwi): manda pela serial as mesmas mensagens JSON que o hub manda (com acentos escapados, como o Python faz) e tira uma captura da tela depois de cada uma.

## Ver ao vivo, ligado ao hub de verdade

1. Instale a extensão **Wokwi Simulator** no VS Code (login com a conta do Wokwi) e abra a pasta `firmware/`.
2. `pio run -e wokwi_s3`, depois **F1 → "Wokwi: Start Simulator"**: a tela simulada aparece e responde ao mouse como touch.
3. Num terminal: `../.venv/bin/python tools/hub_bridge.py`. A ponte entra no hub como um Buddy e liga a serial simulada (exposta em `localhost:4000` pelo `rfc2217ServerPort` do `wokwi.toml`) ao WebSocket do hub: o Buddy simulado mostra as sessões reais, o clima e as piadas, e os toques na tela voltam ao hub.

Sem VS Code também dá: `wokwi-cli . --timeout 60000` + a ponte, mas aí só há capturas (`--screenshot-time`), não a tela ao vivo.

A ponte existe até o firmware ter WiFi + WebSocket próprios.

## WiFi e hub

`src/net/hub_link.cpp` conecta no WiFi e no WebSocket do hub (`/ws?token=…`), reconecta sozinho (WiFi e hub) e manda um heartbeat para notar um hub que sumiu. Enquanto não conecta, o rosto diz o porquê ("conectando ao WiFi…", "procurando o hub…"). O link serial continua ativo junto (testes e ponte).

Configuração, do menor para o maior peso:

1. **Na compilação**, por variáveis de ambiente: `BUDDY_WIFI_SSID`, `BUDDY_WIFI_PASSWORD`, `BUDDY_HUB_HOST`, `BUDDY_HUB_TOKEN` (o ambiente `wokwi_s3` já vem com `Wokwi-GUEST` e `host.wokwi.internal`).
2. **Gravada na placa** (NVS), pela serial USB, sem recompilar — reinicia e passa a valer:
   ```
   {"type":"_config","ssid":"MinhaRede","password":"…","host":"192.168.0.10","port":8765,"token":"…"}
   ```
   (Depois vem a configuração pelo celular, com a rede "Buddy-setup".)

**Pelo celular (primeiro uso, ou ⚙ → "WiFi e hub" → Configurar):** o Buddy abre a rede WiFi **Buddy-setup**; conecte o celular, abra `192.168.4.1`, escolha o WiFi de casa e informe o IP do hub (o Ubuntu na rede de casa), a porta e o token. Ele salva e reinicia. (WiFiManager; não roda no Wokwi, que não cria pontos de acesso.)

**Atualização sem cabo (OTA):** com o Buddy no WiFi, `BUDDY_DEVICE_IP=<ip do Buddy> BUDDY_HUB_TOKEN=$(cat ~/.config/claude-buddy/token) pio run -e m5_cores3_ota -t upload`. O token do hub é a senha; a tela mostra "atualizando o firmware…".

**No Wokwi gratuito o WiFi funciona, mas o hub não é alcançável:** `host.wokwi.internal` depende do gateway privado, que é só dos planos pagos. O WebSocket é validado no CoreS3; no simulador, use a ponte serial.

**Para o CoreS3 alcançar o hub** ele precisa escutar na rede local (`--host 0.0.0.0`, hoje é só `127.0.0.1`). Isso fica para quando a placa chegar, com o cuidado de aceitar só a rede de casa: o Ubuntu é um notebook de trabalho que pode estar em outras redes.

## O que o Wokwi não cobre (fica para a placa real)

- Áudio (microfone I2S, alto-falante).
- Controlador de tela e touch exatos da NM-CYD-C5 (ILI9341 ou ST7789; touch resistivo ou capacitivo).
- O ESP32-C5 em si (alpha no Wokwi): simulamos no ESP32-S3, mesmo código de UI e protocolo.
- WiFi 5 GHz, consumo, aquecimento.

## Peculiaridades descobertas

- **O ILI9341 do Wokwi ignora os bits de espelhamento do MADCTL** (só respeita a troca linha/coluna): toda rotação aparece espelhada. `hal/lvgl_port.cpp` espelha pixels e toque **só no Wokwi** (`kMirrorX`); um painel real respeita os bits.
- **As fontes da LVGL são só ASCII.** `tools/make_fonts.sh` gera DejaVu Sans Mono com Latin-1 + os símbolos da interface, e corrige a verificação de versão que o `lv_font_conv` (feito para a LVGL 8) faz errado na LVGL 9.
- **Buffer serial:** o padrão de 256 bytes transborda com as mensagens de estado enquanto a LVGL desenha; `main.cpp` usa 8 KB.
- O `write-serial` dos cenários do Wokwi trava com caracteres fora do ASCII; o hub manda JSON escapado (`é`), que é o que os testes usam.
