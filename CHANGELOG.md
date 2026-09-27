# Changelog

Todas as mudanças relevantes deste projeto são documentadas aqui.
O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/)
e o projeto adere a [Semantic Versioning](https://semver.org/lang/pt-BR/).

## [Unreleased]

### Corrigido
- Erro de TDZ no cliente (`_waitForIceGathering` referenciava `done` antes
  da declaração) que impedia o envio da oferta SDP pelo navegador do celular.
- Crash no servidor com aiortc >= 1.14 em ofertas apenas de vídeo: o
  transceiver de áudio pré-declarado ficava sem m-line correspondente e
  `setLocalDescription` falhava com `ValueError` em toda conexão. O
  transceiver agora é criado implicitamente na renegociação do microfone.
- Navegadores do celular podiam servir JavaScript desatualizado do cache:
  `/`, `/js/` e `/css/` respondem com `Cache-Control: no-store` e as URLs
  dos módulos são versionadas.
- Erros inesperados de sinalização agora registram traceback completo.

## [2.0.0] - 2026-09-13

Reescrita completa com transporte **WebRTC** (H.264/Opus) no lugar de
JPEG quadro-a-quadro via WebSocket.

### Adicionado
- Vídeo via WebRTC/aiortc: codificação H.264 por hardware no celular,
  ~5x menos banda e latência muito menor que MJPEG, adaptação automática
  de bitrate (congestion control do WebRTC).
- Áudio como trilha Opus na mesma conexão, o que elimina o canal PCM
  separado e o ScriptProcessorNode obsoleto.
- Autenticação por primeira mensagem do WebSocket (PIN fora da URL),
  comparação constant-time, rate limiting (5 falhas: lockout de 60s),
  validação de Origin, limite de tamanho de mensagem e CSP/nosniff headers.
- QR code sem PIN por padrão (`--qr-pin` opt-in para embedar).
- Flag `--bitrate` e negociação com `degradationPreference:
  maintain-framerate`.
- Watchdog de sessão (15s sem vídeo encerra), kick do client anterior
  (vídeo+áudio compartilham uma PeerConnection, o que resolve o bug de
  áudio corrompido com 2 clients), stats periódicos (fps/bitrate/perda) no
  terminal e no overlay do celular.
- Zoom nativo da câmera via `applyConstraints` (fallback: crop digital
  via canvas.captureStream), controle de lanterna (torch), overlay de
  estatísticas com um toque, atalhos de teclado no desktop.
- Pacote Python modular (`phonecam/`), frontend em ES modules,
  logging estruturado, constantes nomeadas, CI (ruff/black/pip-audit).
- README reescrito (instalação/execução reais do zero).

### Alterado
- `server.py` monolítico (~1450 linhas) dividido no pacote `phonecam/` por
  responsabilidade; `index.html` único separado em HTML/CSS/JS.
- Reconexão exibida como barra semi-transparente (não cobre o vídeo).
- Toggle de microfone simplificado para 2 estados (ON/OFF).
- Acessibilidade: zoom de página permitido, alvos de toque ≥48px,
  aria-labels, `prefers-reduced-motion`.

### Removido
- **Breaking:** pipeline MJPEG (`/ws`, `/ws/audio`, loop canvas/toBlob).
  Navegadores sem WebRTC não são mais suportados.
- Scripts de instalação/remoção duplicados (install.sh/install.bat/
  install.py/uninstall.*/run.bat/run.sh), repositório simplificado
  para `run.py` + `python -m phonecam`.

## [1.0.0] - 2025-01-15

- Versão inicial: streaming MJPEG via WebSocket, PIN na URL,
  áudio PCM via canal separado, QR code com PIN embutido.
