# Eva — the IDEVA AI assistant

Desktop AI assistant you can talk to by text and voice, in Spanish.
Built with Python 3.11 and Flet.

## Development setup (Phase 0)

Requirements: Python 3.11, Git, and a local model server (LM Studio on port 1234 or Ollama on port 11434).

```bash
py -3.11 -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
python app/main.py             # opens the "Hola mundo" test window
```

Check the local model server (LM Studio):

```bash
lms server start
curl http://localhost:1234/v1/models
```

## Layout

- `docs/` — work plan (`plan-de-trabajo.md` / `.pdf`) and visual references (`referencias/`: brand guide and motion guide)
- `app/` — application code (UI, LLM clients, voice, memory)
- `assets/` — icon, sounds and Eva layers
- `bin/` — Engram binaries per OS (downloaded, not committed)
- `installer/` — installer scripts per OS
- `docker/` — optional Docker deployment
- `tests/` — pytest tests
