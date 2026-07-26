# Contribuindo para o PhoneCam

Obrigado por considerar contribuir! 🎉

## Como Contribuir

### 1. Reportar Bugs
- Use o template de **Bug Report** nas Issues
- Inclua: OS, Python version, passos para reproduzir, logs de erro

### 2. Sugerir Funcionalidades
- Use o template de **Feature Request**
- Explique o problema que resolve e caso de uso

### 3. Pull Requests
1. Fork o repo
2. Crie uma branch: `git checkout -b feature/minha-feature`
3. Faça commits pequenos e claros
4. Rode os testes: `make test` (ou `pytest`)
5. Abra o PR com descrição clara

## Padrões de Código

### Python (Backend)
- **Style**: Black + Ruff (config em `pyproject.toml`)
- **Type hints**: Obrigatórios em funções públicas
- **Docstrings**: Google style para funções públicas
- **Testes**: Pytest, cobrir novos comportamentos

```bash
# Format + lint
ruff check --fix .
black .

# Testes
pytest -v
```

### HTML/JS (Frontend mobile)
- **Style**: Prettier (single quotes, 2 spaces)
- **ES6+ modules** (sem bundler necessário)
- **Sem framework** — vanilla JS puro
- **Compatibilidade**: Chrome 90+, Safari 14+, Firefox 88+

```bash
# Format (se tiver prettier)
npx prettier --write static/index.html
```

## Estrutura do Projeto

```
phone-webcam/
├── server.py           # FastAPI + WebSocket + virtual cam/mic
├── static/index.html   # Página mobile (getUserMedia + WS)
├── requirements.txt    # Deps Python
├── install.py          # Instalador multiplataforma
├── run.py              # Launcher multiplataforma
├── run.sh / run.bat    # Wrappers shell
└── certs/              # SSL auto-assinado (gerado em runtime)
```

## Testando Localmente

```bash
# 1. Instala deps
python install.py --skip-system

# 2. Roda (Linux/macOS)
./run.sh --width 640 --height 480 --fps 24

# 3. No celular: mesma Wi-Fi, abra https://<ip-pc>:8765/?pin=XXXXX
#    Aceite o certificado auto-assinado
```

## Convenções de Commit

```
feat: adiciona suporte a 4K
fix: corrige reconexão em iOS Safari
docs: atualiza README com instruções Windows
refactor: simplifica audio pipeline
test: adiciona testes para virtual mic
chore: atualiza dependências
```

## Code Review Checklist

- [ ] Testes passam
- [ ] Lint/format ok
- [ ] Documentação atualizada (README, docstrings)
- [ ] Changelog atualizado (se aplicável)
- [ ] Sem segredos/credenciais no código
- [ ] Compatível Windows/macOS/Linux

## Dúvidas?

Abra uma **Discussion** ou issue com label `question`.

---

**Obrigado por melhorar o PhoneCam!** 📱🎥