"""
Lê o FPS de um jogo com o PresentMon (Intel), rodando escondido em segundo plano.
Precisa que o usuário esteja no grupo "Usuários de log de desempenho" (ou rodar como admin).
"""
import collections
import subprocess
import threading
import time
from pathlib import Path

PRESENTMON = Path(__file__).resolve().parent / "tools" / "PresentMon.exe"
CREATE_NO_WINDOW = 0x08000000


class FpsReader:
    def __init__(self):
        self.pid = None
        self.proc = None
        self.frames = collections.deque()   # (instante, ms entre quadros)
        self.lock = threading.Lock()
        self.error = None                   # "sem permissão" etc.

    def watch(self, pid):
        """Começa a medir o jogo `pid` (ou para, com None)."""
        if pid == self.pid:
            if pid and self.proc and self.proc.poll() is not None and not self.error:
                self._start(pid)            # PresentMon caiu: reabre
            return
        self.stop()
        if pid:
            self._start(pid)

    def _start(self, pid):
        self.pid, self.error = pid, None
        with self.lock:
            self.frames.clear()
        if not PRESENTMON.exists():
            self.error = "PresentMon não encontrado"
            return
        self.proc = subprocess.Popen(
            [str(PRESENTMON), "--process_id", str(pid), "--output_stdout", "--v1_metrics",
             "--no_console_stats", "--session_name", "TuringScreenFPS", "--stop_existing_session",
             "--terminate_on_proc_exit", "--no_track_display", "--no_track_input", "--no_track_gpu"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW,
            text=True, encoding="utf-8", errors="ignore")
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        threading.Thread(target=self._read_err, args=(self.proc,), daemon=True).start()

    def _read_err(self, proc):
        for ln in proc.stderr:
            if "access denied" in ln.lower() or "performance log users" in ln.lower():
                self.error = "sem permissão"

    def _read(self, proc):
        col = None
        for ln in proc.stdout:
            parts = ln.rstrip("\n").split(",")
            if col is None:
                if "MsBetweenPresents" in parts:
                    col = parts.index("MsBetweenPresents")
                continue
            try:
                ms = float(parts[col])
            except (ValueError, IndexError):
                continue
            if ms <= 0:
                continue
            now = time.monotonic()
            with self.lock:
                self.frames.append((now, ms))
                while self.frames and now - self.frames[0][0] > 10:
                    self.frames.popleft()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.proc, self.pid = None, None
        with self.lock:
            self.frames.clear()

    def read(self):
        """(fps do último segundo, 1% low dos últimos 10 s) ou (None, None)."""
        now = time.monotonic()
        with self.lock:
            recent = [ms for t, ms in self.frames if now - t <= 1.0]
            allf = [ms for _, ms in self.frames]
        if not recent:
            return None, None
        fps = 1000.0 / (sum(recent) / len(recent))
        worst = sorted(allf, reverse=True)[:max(1, len(allf) // 100)]
        low = 1000.0 / (sum(worst) / len(worst))
        return fps, low
