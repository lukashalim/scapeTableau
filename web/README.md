# EscapeTableau web UI

Next.js App Router client for the Python conversion engine.

```bash
npm install
npm run dev
```

The dev script binds hostname `0.0.0.0` on port **4317**. Python 3.11+ and `pip install -r ../requirements.txt` are required so `POST /api/transpile` can spawn `python cli.py process - --format json`.

See the repository root README for CLI, Streamlit, and engine usage.
