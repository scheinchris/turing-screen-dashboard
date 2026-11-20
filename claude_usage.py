"""
Conta os tokens gastos no Claude Code desde a última terça às 10h (início da semana do limite),
lendo as conversas salvas em ~/.claude/projects. Leitura incremental: só lê o que é novo.
"""
import datetime as dt
import json
import time
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"
USAGE_FILE = Path.home() / ".claude" / "turing-screen" / "usage.json"  # % do limite (opcional)


def week_start(now=None):
    now = now or dt.datetime.now()
    start = (now - dt.timedelta(days=(now.weekday() - 1) % 7)).replace(hour=10, minute=0, second=0, microsecond=0)
    if start > now:
        start -= dt.timedelta(days=7)
    return start


def _parse_ts(s):
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except Exception:
        return None


class TokenCounter:
    def __init__(self):
        self.start = None
        self.files = {}   # path -> offset lido
        self.seen = set()
        self.total = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}

    def _reset(self, start):
        self.start = start
        self.files.clear()
        self.seen.clear()
        for k in self.total:
            self.total[k] = 0

    def update(self):
        ws = week_start()
        if ws != self.start:
            self._reset(ws)
        cutoff = ws.timestamp()
        for f in PROJECTS.rglob("*.jsonl"):
            try:
                st = f.stat()
            except OSError:
                continue
            if st.st_mtime < cutoff:
                continue
            off = self.files.get(f, 0)
            if st.st_size <= off:
                continue
            with f.open("rb") as fh:
                fh.seek(off)
                chunk = fh.read()
            end = chunk.rfind(b"\n")
            if end < 0:
                continue
            self.files[f] = off + end + 1
            for ln in chunk[:end].split(b"\n"):
                if b'"usage"' not in ln:
                    continue
                try:
                    e = json.loads(ln)
                except Exception:
                    continue
                m = e.get("message") or {}
                u = m.get("usage")
                if not u or e.get("type") != "assistant":
                    continue
                key = (m.get("id"), e.get("requestId"))
                if key in self.seen:
                    continue
                ts = _parse_ts(e.get("timestamp", ""))
                if ts is None or ts < ws:
                    continue
                self.seen.add(key)
                self.total["input"] += u.get("input_tokens", 0) or 0
                self.total["output"] += u.get("output_tokens", 0) or 0
                self.total["cache_write"] += u.get("cache_creation_input_tokens", 0) or 0
                self.total["cache_read"] += u.get("cache_read_input_tokens", 0) or 0
        return self.total

    def spent(self):
        """Tokens 'gastos' = entrada + saída + escrita de cache (leitura de cache é quase de graça)."""
        t = self.total
        return t["input"] + t["output"] + t["cache_write"]


def read_limit():
    """Última leitura real do % do limite (usage.json)."""
    try:
        return json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def fetch_limit(tokens_now):
    """Pergunta o uso do plano à Anthropic com o login do Claude Code salvo no PC.
    Só funciona enquanto esse login estiver válido; o token nunca é gravado nem mostrado."""
    import urllib.request
    cred = Path.home() / ".claude" / ".credentials.json"
    tok = json.loads(cred.read_text(encoding="utf-8"))["claudeAiOauth"]["accessToken"]
    req = urllib.request.Request("https://api.anthropic.com/api/oauth/usage", headers={
        "Authorization": "Bearer " + tok, "anthropic-beta": "oauth-2025-04-20"})
    with urllib.request.urlopen(req, timeout=15) as r:
        j = json.loads(r.read().decode())
    week, five = j.get("seven_day") or {}, j.get("five_hour") or {}
    d = {"weekly_percent": week.get("utilization"), "five_hour_percent": five.get("utilization"),
         "resets_at": week.get("resets_at"), "tokens_at": tokens_now, "updated": time.time(),
         "source": "api"}
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    USAGE_FILE.write_text(json.dumps(d), encoding="utf-8")
    return d


def estimate(limit, tokens_now, week_start_ts):
    """% atual: real se recente, senão estimado pelos tokens, calibrado pela última leitura real."""
    if not limit or limit.get("weekly_percent") is None:
        return None, False
    pct, upd = limit["weekly_percent"], limit.get("updated", 0)
    if upd < week_start_ts:          # leitura é da semana passada: sem calibração válida
        return None, False
    if time.time() - upd < 15 * 60:
        return pct, False
    base = limit.get("tokens_at") or 0
    if base > 0 and tokens_now:
        return min(100.0, pct * tokens_now / base), True
    return pct, True


if __name__ == "__main__":
    t0 = time.time()
    c = TokenCounter()
    tot = c.update()
    print("semana desde", c.start, "->", tot, "gastos:", c.spent(), f"({time.time() - t0:.1f}s)")
    t0 = time.time()
    c.update()
    print(f"2ª leitura incremental: {time.time() - t0:.2f}s")
