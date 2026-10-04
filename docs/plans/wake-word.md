# Plano: palavra de ativação "Ei, Buddy"

**Estado:** planejado, ainda não começado (decidido em 2026-10-04: fazer assim que possível).
**Objetivo:** o Buddy (CoreS3) começa a ouvir quando você diz "Ei, Buddy", sem tocar na tela, e sem mandar áudio para lugar nenhum enquanto espera a palavra.

## Por que não o ESP-SR da Espressif

O WakeNet (ESP-SR) do ESP32-S3 só reconhece palavras treinadas pela própria Espressif ("Hi ESP"…); palavras novas são um serviço deles, não dá para treinar em casa. Enquanto o plano não sai, **segurar o rosto** faz o papel da palavra de ativação.

## Abordagem: microWakeWord

- Projeto aberto (https://github.com/kahrendt/microWakeWord), feito para palavras de ativação **no ESP32-S3**; é o que o ESPHome / Home Assistant usam nos seus aparelhos de voz.
- Modelo TFLite Micro de ~50 KB, rodando contínuo no chip; só depois da palavra o áudio vai ao hub (como no "segurar o rosto").
- Treino com amostras **sintéticas** (piper-sample-generator: milhares de vozes dizendo a frase) misturadas com ruído de fundo, mais **gravações reais** do Michael para robustez.
- **Frase:** treinar **"Hey Buddy"**. O gerador de amostras é em inglês, e "Hey Buddy" soa praticamente igual a "Ei, Buddy"; as gravações reais (em português) cobrem a diferença.

## Etapas

| # | Etapa | Onde | Quem | Estimativa |
|---|---|---|---|---|
| 1 | WSL2 + Ubuntu + driver CUDA no desktop (`wsl --status`; o TensorFlow atual não usa GPU no Windows nativo) | desktop | Michael roda, Claude guia | ~30 min |
| 2 | Ambiente de treino do microWakeWord no WSL2 (Python, TensorFlow com CUDA, piper-sample-generator) | desktop | Claude | ~30 min |
| 3 | Gerar amostras positivas ("Hey Buddy", vozes e velocidades variadas) e baixar os conjuntos de negativos/ruído (alguns GB: fala, música, ambiente) | desktop | Claude | ~1 h (download + geração) |
| 4 | Treinar na RTX 3070 Ti e exportar o `.tflite` (streaming) | desktop, GPU | Claude | ~1-2 h |
| 5 | Gravar ~50 amostras reais do Michael ("Ei, Buddy" / "Hey, Buddy", perto e longe do mic, com ruído de escritório) + ~10 min de áudio "normal" sem a palavra, e retreinar | Ubuntu (F9) + desktop | Michael grava (~20 min), Claude retreina | ~1 h |
| 6 | Avaliar no Ubuntu com o microfone: taxa de acerto e **falsos alarmes por hora** (deixar rodando durante reuniões/trabalho normal) | Ubuntu | Claude | 1 dia rodando |
| 7 | Integrar no firmware do CoreS3: detector em streaming (porte do componente `micro_wake_word` do ESPHome + esp-tflite-micro), microfone contínuo em baixa prioridade, e ao detectar → mesmo fluxo do "segurar o rosto" | firmware | Claude | a parte mais trabalhosa; só valida com a placa |
| 8 | Ajustar o limiar de detecção na placa e decidir o comportamento no modo noite (desligado à noite?) | CoreS3 | ambos | ~1 h |

## Critérios de pronto

- Reconhece "Ei, Buddy" do Michael em ≥ 90% das tentativas a ~1 m do Buddy.
- No máximo ~1 falso alarme a cada algumas horas de trabalho normal (incluindo reuniões).
- Áudio só sai do Buddy depois da palavra; o modelo e as gravações ficam locais.
- Pode ser desligado em ⚙ (preferência `wake_word`), e fica desligado no modo noite.

## Riscos e decisões em aberto

- **Integração no firmware (etapa 7):** o detector do ESPHome é C++/ESP-IDF; portar para o nosso firmware (Arduino + LVGL) pode exigir ajustes de memória e tarefas. Alternativa se travar: firmware ESPHome só para o áudio — pior, porque perde o nosso firmware.
- **Microfone sempre ligado x alto-falante:** no CoreS3 eles dividem o barramento I2S; ouvir continuamente exige pausar a escuta enquanto o Buddy fala (já é assim no ditado).
- **Consumo de bateria:** ouvir o tempo todo gasta mais; medir e talvez só ativar com o Buddy na tomada ou com alguém por perto (sensor de proximidade).
- **Privacidade:** as gravações de treino do Michael ficam só no desktop/Ubuntu; não versionar áudio no git.
