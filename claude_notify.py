"""
Hook do Claude Code -> grava um evento que a telinha mostra.
Uso no settings.json:  python claude_notify.py done     (evento Stop)
                       python claude_notify.py waiting  (evento Notification)
Nunca falha: qualquer erro é ignorado pra não atrapalhar a sessão.
"""
import json
import re
import sys
import time
from pathlib import Path

OUT = Path.home() / ".claude" / "turing-screen" / "events.jsonl"


def last_assistant_text(transcript):
    try:
        p = Path(transcript)
        with p.open("rb") as f:
            f.seek(max(0, p.stat().st_size - 400_000))
            lines = f.read().decode("utf-8", "ignore").splitlines()
        for ln in reversed(lines):
            try:
                e = json.loads(ln)
            except Exception:
                continue
            if e.get("type") != "assistant":
                continue
            for c in e.get("message", {}).get("content", []):
                if c.get("type") == "text" and c.get("text", "").strip():
                    return c["text"]
    except Exception:
        pass
    return ""


def clean(t):
    for ln in t.splitlines():
        ln = re.sub(r"[*_`#>\[\]]", "", ln).strip(" -•")
        if ln:
            return ln[:120]
    return ""


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else "done"
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "ignore") or "{}")
    except Exception:
        data = {}

    if kind == "waiting":
        m = data.get("message", "")
        low = m.lower()
        if "permission" in low:
            msg = "precisa da sua permissão"
        elif "waiting for your input" in low or "idle" in low:
            msg = "esperando sua resposta"
        else:
            msg = m or "esperando você"
    else:
        msg = clean(data.get("last_assistant_message") or last_assistant_text(data.get("transcript_path", ""))) or "terminou"

    cwd = data.get("cwd") or ""
    project = Path(cwd).name if cwd else "sessão"

    ev = {"ts": time.time(), "session": data.get("session_id", ""), "type": kind,
          "project": project, "message": msg}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines = OUT.read_text(encoding="utf-8").splitlines()[-199:] if OUT.exists() else []
    lines.append(json.dumps(ev, ensure_ascii=False))
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
