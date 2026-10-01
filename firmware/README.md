# Firmware do Claude Buddy

C++ (Arduino core 3.x via pioarduino) + LVGL 9 + LovyanGFX + ArduinoJson. O simulador do navegador (`../simulator/index.html`) continua sendo a referência de comportamento e visual.

| Pasta | O que é |
|---|---|
| `src/app/` | Estado, protocolo do hub e regras do rosto. C++ puro, testado no host (`pio test -e native`). |
| `src/hal/` | Tela, touch e a ponte com a LVGL. **Só aqui muda de uma placa para outra.** |
| `src/ui/` | As telas (LVGL), o sprite do mascote (`sprite.h`, mesma grade do simulador) e as fontes. |
| `src/net/` | Link com o hub. Por enquanto o link serial de teste; o WebSocket vem a seguir. |
| `tools/` | `screens.py` (todas as telas no Wokwi) e `make_fonts.sh` (fontes com acentos). |

## Testar sem hardware

```bash
pio test -e native                                  # protocolo e regras, no Ubuntu
pio run -e wokwi_s3                                 # compila para o ESP32-S3 do Wokwi
WOKWI_CLI_TOKEN=$(cat ~/.config/claude-buddy/wokwi_token) python3 tools/screens.py
# -> wokwi/screenshots/*.png e contact-sheet.png, uma captura por estado
```

`screens.py` roda **uma** simulação (poupa os minutos do plano gratuito do Wokwi): manda pela serial as mesmas mensagens JSON que o hub manda (com acentos escapados, como o Python faz) e tira uma captura da tela depois de cada uma.

## Ver ao vivo, ligado ao hub de verdade

1. Instale a extensão **Wokwi Simulator** no VS Code (login com a conta do Wokwi) e abra a pasta `firmware/`.
2. `pio run -e wokwi_s3`, depois **F1 → "Wokwi: Start Simulator"**: a tela simulada aparece e responde ao mouse como touch.
3. Num terminal: `../.venv/bin/python tools/hub_bridge.py`. A ponte entra no hub como um Buddy e liga a serial simulada (exposta em `localhost:4000` pelo `rfc2217ServerPort` do `wokwi.toml`) ao WebSocket do hub: o Buddy simulado mostra as sessões reais, o clima e as piadas, e os toques na tela voltam ao hub.

Sem VS Code também dá: `wokwi-cli . --timeout 60000` + a ponte, mas aí só há capturas (`--screenshot-time`), não a tela ao vivo.

A ponte existe até o firmware ter WiFi + WebSocket próprios.

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
