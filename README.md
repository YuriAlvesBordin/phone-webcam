# PhoneCam

Use a **câmera do seu celular** como uma **webcam virtual** no PC — funciona em **Windows, macOS e Linux**. Compatível com Discord, OBS Studio, Zoom, Google Meet, Teams, Cheese e qualquer app que aceite dispositivos de webcam.

Sem app nativo no celular: tudo roda pelo navegador (Chrome, Safari, Firefox). Funciona em **Android e iPhone**.

Suporte **opcional** a microfone do celular como microfone virtual no PC.

---

## ✨ Características

- 🌐 **Multiplataforma** — Windows 10+, macOS 11+, Ubuntu/Debian, Fedora, Arch Linux
- 📱 **Sem app no celular** — abre uma página no navegador
- 🍎 **Android + iPhone** — usa `getUserMedia` padrão Web
- 🎥 **HD 1280×720 @ 30fps** (configurável até 1080p)
- 📐 **Sem distorção** — crop inteligente (object-fit: cover) funciona em portrait e landscape
- 🔍 **Zoom 1×–4×** — slider, botões +/−, **pinch-to-zoom** (gesto natural) e scroll do mouse
- ✋ **Pan** — arraste o dedo sobre o vídeo quando estiver com zoom aplicado
- 🎤 **Microfone do celular** — usa o mic do celular como microfone virtual no PC (opcional, `--audio`)
- 🔒 **PIN de 6 dígitos** — ninguém na rede conecta sem autorização
- 📲 **QR Code no terminal** — escaneie direto do PC com a câmera do celular
- 💡 **Wake Lock** — mantém a tela do celular acesa enquanto transmite
- 🔄 **Reconexão automática** — recupera sozinho de quedas de Wi-Fi ou tela apagada
- 🔄 **Troca câmera frontal/traseira** com um toque
- ⏸ **Pausar/retomar** stream sem desconectar
- 📊 **FPS em tempo real** no celular e no PC
- 🔌 **Webcam virtual** — via v4l2loopback (Linux) ou OBS Virtual Camera (Windows/macOS)
- 💻 **CLI puro** — sem interface gráfica pesada

---

## 🏗 Arquitetura

```
   Celular (navegador)                      PC (Win/Mac/Linux)
  ┌─────────────────────┐                ┌──────────────────────────┐
  │  getUserMedia()     │                │  FastAPI + uvicorn       │
  │  canvas.toBlob(JPEG)│ ──WebSocket──▶ │  /ws (binary frames)     │
  │  ws.send(jpeg)      │                │  Pillow decodifica       │
  └─────────────────────┘                │  pyvirtualcam.send(BGR)  │
                                          │  → /dev/video10 (Linux) │
                                          │  → OBS Virtual Cam (Win)│
                                          │  → OBS Virtual Cam (Mac)│
                                          └────────┬─────────────────┘
                                                   │
                                          Discord / OBS / Zoom ──▶ webcam virtual
```

**Backends por OS**:

| OS | Webcam virtual | Microfone virtual (opcional) |
|----|----------------|------------------------------|
| **Linux** | `v4l2loopback` (auto-instalado) | PulseAudio/PipeWire null sink (auto-criado) |
| **Windows** | OBS Virtual Camera (instalar OBS Studio) | VB-Cable (instalar manualmente) |
| **macOS** | OBS Virtual Camera (instalar OBS Studio) | BlackHole (instalar manualmente) |

---

## 📦 Instalação

### Pré-requisitos

- **Python 3.9+** instalado (https://python.org)
- Celular e PC na **mesma rede Wi-Fi**
- Navegador moderno no celular (Chrome 90+, Safari 14+, Firefox 88+)
- **No Windows/macOS**: instalar [OBS Studio](https://obsproject.com/download) e ativar "Virtual Camera" uma vez (para a webcam virtual funcionar)
- **Para microfone do celular (opcional)**:
  - Linux: nenhum extra (PulseAudio/PipeWire já vem instalado)
  - Windows: instalar [VB-Cable](https://vb-audio.com/Cable/)
  - macOS: instalar [BlackHole](https://existential.audio/blackhole/)

### Windows

```cmd
:: 1) Descompacte o phone-webcam em qualquer pasta
cd phone-webcam

:: 2) Instale (detecta tudo automaticamente)
install.bat

:: 3) Rode
run.bat
```

### macOS e Linux

```bash
# 1) Descompacte o phone-webcam em qualquer pasta
cd phone-webcam

# 2) Torne os scripts executáveis (só na primeira vez)
chmod +x install.sh run.sh install.py run.py

# 3) Instale
./install.sh

# 4) Rode
./run.sh
```

### Instalação alternativa (qualquer OS, via Python direto)

```bash
python install.py        # instala
python run.py            # roda
```

---

## 🚀 Uso

### Iniciar

```bash
# Linux/macOS
./run.sh

# Windows
run.bat

# Qualquer OS (via Python direto)
python run.py
```

O terminal exibirá algo como:

```
================================================================
  PhoneCam — Celular como Webcam no PC
================================================================

  OS        : Linux
  Resolução : 1280x720 @ 30fps
  PIN acesso: 482910
  Protocolo : HTTPS
  Cert SSL  : /home/.../phonecam/certs/phonecam.pem
  Microfone : desativado (use --audio para ativar)

  ┌─ Escaneie o QR Code com a câmera do celular ─┐
  │  (aponte para http://... acima se falhar)    │
  └──────────────────────────────────────────────┘

      █▀▀▀▀▀█ ▄▄  ▄ ▀▄█ █▀▀▀▀▀█
      █ ███ █ █▄██▄█ ▀▄ █ ███ █
      ... (QR Code renderizado em ASCII art)

  URL embutida no QR: https://192.168.1.42:8765/

  ⚠  AVISO DE CERTIFICADO: o navegador vai mostrar 'Conexão não
     segura'. É NORMAL — aceite para continuar.
```

> **Por que HTTPS?** Navegadores só liberam `getUserMedia` (acesso à câmera) em contexto seguro (HTTPS ou `localhost`). O PhoneCam gera automaticamente um certificado SSL auto-assinado em `certs/` na primeira execução.

### No celular

1. Conecte-se na **mesma rede Wi-Fi** do PC
2. **Escaneie o QR Code** exibido no terminal do PC com a câmera do celular — o PIN já vem embutido na URL, então a página conecta **automaticamente** sem precisar digitar o PIN
3. **Aceite o aviso de certificado**:
   - **Chrome Android**: "Sua conexão não é privada" → "Avançado" → "Continuar para 192.168.x.x (não seguro)"
   - **Safari iOS**: "Este site não é seguro" → "Mostrar detalhes" → "Visitar este site"
   - **Firefox Android**: "Aviso de risco potencial" → "Avançado" → "Aceitar o risco e continuar"
4. Se não escaneou o QR, abra `https://<ip-do-pc>:8765/?pin=XXXXXX` manualmente (ou digite o PIN na tela)
5. Permita o acesso à câmera quando solicitado
6. Pronto — a câmera está transmitindo

### Nos apps (Discord, OBS, etc.)

| App | Como selecionar a webcam |
|-----|--------------------------|
| **Discord** | Configurações → Voz e Vídeo → Dispositivo de vídeo → **OBS Virtual Camera** (Win/Mac) ou **PhoneCam** (Linux) |
| **OBS** | + em Fontes → Dispositivo de captura de vídeo → **OBS Virtual Camera** ou **PhoneCam** |
| **Zoom** | Settings → Video → Camera → **OBS Virtual Camera** ou **PhoneCam** |
| **Google Meet / Teams** | Settings → Video → **OBS Virtual Camera** ou **PhoneCam** |

### Controles no celular

| Ação | Como fazer |
|------|------------|
| **Zoom in/out** | Slider, botões **−** / **+**, ou **pinch** com 2 dedos sobre o vídeo |
| **Resetar zoom** | Botão **1.0×** (canto inferior direito) |
| **Mover área zoomada** | Arraste 1 dedo sobre o vídeo (só funciona com zoom > 1×) |
| **Trocar câmera** | Botão **🔄 Trocar câmera** |
| **Pausar stream** | Botão **⏸ Pausar** |
| **Microfone** | Botão **🎤 Microfone OFF/ON/MUTE** (só se `--audio` ativado) |

---

## ⚙️ Opções de linha de comando

```bash
./run.sh [opções]        # Linux/macOS
run.bat [opções]         # Windows
python run.py [opções]   # qualquer OS

Opções:
  --host 0.0.0.0       # Bind (default: 0.0.0.0 = todas as interfaces)
  --port 8765          # Porta TCP (default: 8765)
  --width 1280         # Largura (default: 1280)
  --height 720         # Altura (default: 720)
  --fps 30             # FPS alvo (default: 30)
  --pin 123456         # PIN fixo (default: aleatório a cada execução)
  --no-https           # Desativa HTTPS (SÓ para localhost; celulares exigem HTTPS)
  --no-audio           # Desativa microfone do celular (áudio é LIGADO por padrão)
```

### Exemplos

```bash
# Padrão: vídeo HD + HTTPS + QR Code + microfone
./run.sh

# Só vídeo (sem microfone do celular)
./run.sh --no-audio

# Full HD 1080p
./run.sh --width 1920 --height 1080

# Porta diferente + PIN fixo (útil para automação)
./run.sh --port 9000 --pin 246810

# SD 480p (rede Wi-Fi fraca)
./run.sh --width 640 --height 480 --fps 24

# Testar localmente sem HTTPS (apenas este PC, sem celular)
./run.sh --no-https --host 127.0.0.1
```

---

## 🎤 Usando o microfone do celular

Por padrão, o PhoneCam já ativa **vídeo + microfone**. Para usar só vídeo, use `--no-audio`.

### Pré-requisitos por OS

| OS | Software necessário | Como instalar |
|----|---------------------|---------------|
| **Linux** | Nenhum extra | PulseAudio/PipeWire já vem instalado |
| **Windows** | [VB-Cable](https://vb-audio.com/Cable/) | Baixe e rode `VBCABLE_Setup_x64.exe` como admin, reinicie o PC |
| **macOS** | [BlackHole 2ch](https://existential.audio/blackhole/) | Baixe o .pkg e instale |

### No celular

Depois de conectar (PIN + câmera), toque em **🎤 Microfone OFF**:

- **1º toque**: liga o microfone (botão fica verde "🎤 Microfone ON")
- **2º toque**: muta (botão fica "🔇 Microfone MUTE")
- **3º toque**: desliga tudo (volta para "🎤 Microfone OFF")

### Nos apps, selecione o microfone

| OS | Nome do dispositivo no Discord/OBS/Zoom |
|----|------------------------------------------|
| **Linux** | **Monitor of PhoneCam Mic** (ou "PhoneCam Mic") |
| **Windows** | **CABLE Output** (VB-Audio Virtual Cable) |
| **macOS** | **BlackHole 2ch** |

---

## 🛠 Solução de problemas

### "malloc(): invalid size (unsorted)" no Linux (PyAudio crash)

Esse é um bug conhecido do **pyaudio 0.2.14 com Python 3.14 + PipeWire** no Linux. O PhoneCam agora tem **fallback automático** para `paplay` (PulseAudio CLI):

1. Se o PyAudio crashar ao iniciar, o servidor automaticamente usa `paplay` como subprocess
2. Você verá no log: `[OK] Microfone virtual criado: 'PhoneCam Mic' (via paplay CLI)`
3. O áudio flui: celular → WS → stdin do paplay → null sink → Discord/OBS

O `paplay` faz parte do pacote `libpulse` (já vem instalado na maioria das distros). Se não estiver:

```bash
sudo pacman -S libpulse          # Arch
sudo apt install pulseaudio-utils  # Ubuntu/Debian
sudo dnf install pulseaudio-utils  # Fedora
```

### Spam de erros "ALSA lib pcm.c: Unknown PCM cards.pcm.rear"

É inofensivo. O PhoneCam agora **silencia automaticamente** esses warnings via `snd_lib_error_set_handler` no ALSA. Se ainda aparecerem, ignore — não afetam a funcionalidade.

### "Não foi possível criar a webcam virtual"

| OS | Solução |
|----|---------|
| **Linux** | `sudo modprobe v4l2loopback exclusive_caps=1 video_nr=10 card_label="PhoneCam"` |
| **Windows** | Instale o [OBS Studio](https://obsproject.com/download), abra uma vez e ative "Start Virtual Camera" |
| **macOS** | Instale o [OBS Studio](https://obsproject.com/download), abra uma vez e ative "Virtual Camera" |

### "Câmera não abre no celular" / `getUserMedia undefined`

Causa: navegador bloqueia câmera via HTTP remoto. **Solução**: use `https://` (com certificado auto-assinado — aceite o aviso). O PhoneCam já ativa HTTPS automaticamente.

### "Tela apagou e a câmera parou"

O PhoneCam tem duas defesas:
1. **Wake Lock API** — tenta manter a tela acesa (Chrome Android 84+, Safari iOS 16.4+)
2. **Reconexão automática** — se a conexão cair, tenta reconectar com backoff exponencial: 1s → 2s → 2s → 5s → 5s → 10s → 10s → 15s

Dicas:
- Mantenha o celular plugado na tomada
- Use Chrome Android para melhor suporte a Wake Lock
- Desative otimização de bateria do navegador para o site

### Discord não mostra a câmera (Linux)

Discord exige `exclusive_caps=1` no v4l2loopback. Verifique:

```bash
cat /sys/module/v4l2loopback/parameters/exclusive_caps
# Deve imprimir: 1
```

Se imprimir `0`, descarregue e recarregue:

```bash
sudo modprobe -r v4l2loopback
sudo modprobe v4l2loopback exclusive_caps=1 video_nr=10 card_label="PhoneCam"
```

### "incompatible constructor arguments" (pyvirtualcam)

O `server.py` faz introspecção da assinatura via `inspect.signature()` e monta a chamada correta automaticamente. Se ainda assim falhar:

```bash
# Linux/macOS
source venv/bin/activate
pip install --upgrade pyvirtualcam

# Windows
venv\Scripts\activate
pip install --upgrade pyvirtualcam
```

### Celular não consegue acessar a URL

1. Confirme que PC e celular estão na **mesma rede Wi-Fi** (não em redes isoladas como "Convidados")
2. Verifique firewall:
   - **Windows**: permitir porta 8765 no Windows Defender Firewall
   - **Linux**: `sudo ufw allow 8765/tcp` (se usar UFW)
   - **macOS**: System Settings → Network → Firewall → permitir
3. Teste com `ping <ip-do-pc>` a partir do celular

### Lag / baixo FPS

- Reduza qualidade: `./run.sh --width 640 --height 480 --fps 24`
- Use Wi-Fi 5GHz em vez de 2.4GHz
- Feche outros apps que usem a rede no celular
- Mantenha o celular próximo ao roteador

### Listar dispositivos de vídeo (Linux)

```bash
v4l2-ctl --list-devices
# Deve mostrar:
# PhoneCam (platform:v4l2loopback-000):
#   /dev/video10
```

### Verificar se a câmera está funcionando

- **Linux**: `ffplay /dev/video10` ou `cheese --device /dev/video10`
- **Windows/macOS**: abra o app Câmera nativo ou OBS Studio e selecione "OBS Virtual Camera"

### Listar dispositivos de áudio

```bash
# Linux
pactl list short sources    # mostra "Monitor of PhoneCam Mic"

# Windows (PowerShell)
# Abra o Painel de Controle → Som → Gravação → deve mostrar "CABLE Output"

# macOS
# System Settings → Sound → Input → deve mostrar "BlackHole 2ch"
```

---

## 🗑 Desinstalar

### Linux

```bash
./uninstall.sh           # remove módulo v4l2loopback + configs
rm -rf venv/             # remove deps Python
```

### Windows / macOS

```bash
# Remove o venv
rm -rf venv/             # macOS/Linux
rmdir /s /q venv         # Windows (cmd)

# Para remover OBS Studio / VB-Cable / BlackHole, desinstale via Painel de Controle (Windows)
# ou arraste o app para o Trash (macOS).
```

---

## 🔒 Segurança

- O PIN é **aleatório a cada execução** (use `--pin` para fixar)
- O servidor escuta em `0.0.0.0` — acessível a qualquer dispositivo na LAN
- **Sem TLS com CA confiável** — frames trafegam com certificado auto-assinado. Para uso em rede corporativa/shared, considere tunnel SSH ou adicionar CA própria
- Apenas **um celular conecta por vez** (conexões novas derrubam as anteriores)
- Não há persistência de PIN, logs ou imagens — tudo é efêmero

---

## 📁 Estrutura do projeto

```
phone-webcam/
├── server.py              # Servidor FastAPI + WebSocket + webcam/mic virtual
├── static/
│   └── index.html         # Página mobile (getUserMedia + WS)
├── requirements.txt       # Dependências Python
├── install.py             # Instalador multiplataforma (Python)
├── install.sh             # Wrapper bash para install.py (Linux/macOS)
├── install.bat            # Wrapper cmd para install.py (Windows)
├── run.py                 # Launcher multiplataforma (Python)
├── run.sh                 # Wrapper bash para run.py (Linux/macOS)
├── run.bat                # Wrapper cmd para run.py (Windows)
├── uninstall.sh           # Remove módulo v4l2loopback (Linux only)
└── README.md              # Este arquivo
```

---

## ❓ FAQ

**Funciona com iPhone?**
Sim, via Safari 14+ ou Chrome iOS. A página usa APIs Web padrão.

**Funciona em Windows 11?**
Sim, testado no Windows 10 e 11. Requer OBS Studio instalado para a webcam virtual.

**Funciona em Mac com Apple Silicon (M1/M2/M3)?**
Sim, suporte nativo. Instale o OBS Studio for Apple Silicon.

**Posso usar 4G em vez de Wi-Fi?**
Não diretamente. O servidor escuta apenas na rede local. Para uso remoto, configure uma VPN (WireGuard/Tailscale) entre o celular e o PC.

**Consume muita bateria do celular?**
Sim — câmera + Wi-Fi + encoding JPEG contínuo. Recomendado celular conectado ao carregador para sessões longas.

**Qual a latência típica?**
- Vídeo: 80-180ms em Wi-Fi 5GHz
- Áudio: 50-120ms
- Em Wi-Fi 2.4GHz pode chegar a 300ms+

**Como funciona o wake lock?**
A página usa `navigator.wakeLock.request("screen")` para pedir ao SO que mantenha a tela acesa. Suportado em Chrome Android 84+ e Safari iOS 16.4+.

---

## 📜 Licença

MIT — use livremente. Attribution apreciada mas não obrigatória.
