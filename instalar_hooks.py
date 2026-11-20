"""
Liga as notificações do Claude Code na telinha: adiciona os hooks Stop e Notification
em ~/.claude/settings.json (faz backup antes e não mexe em nada que já existe lá).

    .venv\\Scripts\\python.exe instalar_hooks.py
"""
import json
import shutil
from pathlib import Path

base = Path(__file__).resolve().parent
pyw = base / ".venv" / "Scripts" / "pythonw.exe"
script = base / "claude_notify.py"
settings = Path.home() / ".claude" / "settings.json"

data = json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else {}
if settings.exists():
    shutil.copy(settings, settings.with_suffix(".json.bak"))

hooks = data.setdefault("hooks", {})
for event, kind in (("Stop", "done"), ("Notification", "waiting")):
    entries = hooks.setdefault(event, [])
    if any(str(script) in json.dumps(e) for e in entries):
        print(f"{event}: já estava ligado")
        continue
    entries.append({"hooks": [{"type": "command", "command": str(pyw), "args": [str(script), kind],
                               "timeout": 10, "async": True}]})
    print(f"{event}: ligado")

settings.parent.mkdir(parents=True, exist_ok=True)
settings.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print("Pronto. Vale para as próximas sessões do Claude Code.")
