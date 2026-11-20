"""
Detecta quando tem um jogo rodando (Windows).

Um programa conta como jogo quando está em primeiro plano e:
  - o .exe fica numa biblioteca de jogos (Steam, Epic, Xbox, GOG, Riot, Ubisoft, EA), ou
  - é um jogo conhecido (Roblox, Minecraft...), ou
  - está em tela cheia usando a GPU forte (pega jogos fora dessas pastas).
Depois de detectado, o modo jogo continua enquanto o jogo estiver aberto e em uso
(em primeiro plano ou com a GPU trabalhando), mesmo se tu der alt+tab rapidinho.
"""
import ctypes
import ctypes.wintypes as wt
import time
from pathlib import Path

import psutil

LIBRARIES = ["\\steamapps\\common\\", "\\epic games\\", "\\xboxgames\\", "\\gog galaxy\\games\\",
             "\\riot games\\", "\\ubisoft game launcher\\games\\", "\\ea games\\", "\\gog games\\"]
KNOWN = {"robloxplayerbeta.exe": "Roblox", "javaw.exe": None, "minecraft.windows.exe": "Minecraft"}
# nunca são jogo, mesmo em tela cheia com GPU alta
NOT_GAMES = {"chrome.exe", "opera.exe", "msedge.exe", "firefox.exe", "brave.exe", "vlc.exe", "mpc-hc64.exe",
             "explorer.exe", "claude.exe", "code.exe", "blender.exe", "unity.exe", "obs64.exe",
             "wallpaper32.exe", "wallpaper64.exe", "steam.exe", "steamwebhelper.exe",
             "epicgameslauncher.exe", "battle.net.exe", "robloxstudiobeta.exe", "premiere pro.exe",
             "davinci resolve.exe", "resolve.exe", "photoshop.exe", "whatsapp.root.exe", "powershell.exe",
             "windowsterminal.exe", "cmd.exe"}

ENTER_SECONDS = 4      # precisa parecer jogo por esse tempo pra entrar
EXIT_SECONDS = 10      # e deixar de parecer por esse tempo pra sair

user32 = ctypes.windll.user32


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


def foreground():
    """(pid, título, tela_cheia) da janela em primeiro plano."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None, "", False
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    mon = user32.MonitorFromWindow(hwnd, 2)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(mon, ctypes.byref(mi))
    m = mi.rcMonitor
    full = (rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom)
    return pid.value, buf.value, full


def game_name(exe, title):
    low = exe.lower()
    for lib in LIBRARIES:
        i = low.find(lib)
        if i >= 0:
            rest = exe[i + len(lib):].split("\\")
            if rest and rest[0]:
                return rest[0]
    name = KNOWN.get(Path(exe).name.lower())
    return name or (title.strip() or Path(exe).stem)


class GameDetector:
    def __init__(self):
        self.game = None          # {"pid", "name", "since"}
        self._cand, self._cand_t = None, 0.0
        self._miss_t = None

    def _looks_like_game(self, gpu):
        pid, title, full = foreground()
        if not pid or pid == psutil.Process().pid:
            return None
        try:
            p = psutil.Process(pid)
            exe = p.exe()
        except Exception:
            return None
        name = Path(exe).name.lower()
        if name in NOT_GAMES:
            return None
        low = exe.lower()
        in_lib = any(lib in low for lib in LIBRARIES)
        known = name in KNOWN and (name != "javaw.exe" or "minecraft" in title.lower())
        heavy_full = full and gpu is not None and gpu >= 50
        if in_lib or known or heavy_full:
            return {"pid": pid, "name": game_name(exe, title)}
        return None

    def update(self, gpu):
        now = time.time()
        if self.game:
            alive = psutil.pid_exists(self.game["pid"])
            fg_pid = foreground()[0]
            busy = gpu is not None and gpu >= 30
            if alive and (fg_pid == self.game["pid"] or busy):
                self._miss_t = None
            else:
                self._miss_t = self._miss_t or now
                if not alive or now - self._miss_t > EXIT_SECONDS:
                    self.game, self._miss_t = None, None
            return self.game

        g = self._looks_like_game(gpu)
        if g and self._cand and g["pid"] == self._cand["pid"]:
            if now - self._cand_t >= ENTER_SECONDS:
                self.game = {**g, "since": self._cand_t}
                self._cand = None
        else:
            self._cand, self._cand_t = g, now
        return self.game


if __name__ == "__main__":
    d = GameDetector()
    for _ in range(10):
        print(foreground(), d.update(0))
        time.sleep(1)
