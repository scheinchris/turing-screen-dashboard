"""
Tela clean para a Turing Smart Screen 3.5" (rev. A) — 480x320 deitada.

Mostra: hora/data, clima da cidade, CPU, RAM, GPU (uso + temperatura)
e as notificações do Claude Code (sessões que terminaram / esperando você).

Uso:
    python screen.py                 -> roda na tela de verdade
    python screen.py --preview a.png -> só gera uma imagem de prévia
"""
import argparse
import json
import math
import signal
import sys
import threading
import time
import datetime as dt
from pathlib import Path

import numpy as np
import psutil
import requests
from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent
REPO = BASE / "turing-smart-screen-python"
sys.path.insert(0, str(REPO))

# ----------------------------------------------------------------- config (config.json)
try:
    _CFG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
except Exception:
    _CFG = {}
COM_PORT = _CFG.get("com_port", "AUTO")
BRIGHTNESS = _CFG.get("brightness", 30)            # rev. A esquenta acima de ~50%
CITY = _CFG.get("city", "São Paulo")
LAT, LON = _CFG.get("lat", -23.5505), _CFG.get("lon", -46.6333)
TIMEZONE = _CFG.get("timezone", "America/Sao_Paulo")
EVENTS_FILE = Path.home() / ".claude" / "turing-screen" / "events.jsonl"
EVENT_MAX_AGE_H = _CFG.get("event_max_age_hours", 12)   # notificações mais velhas que isso somem
OVERLAY_SECONDS = _CFG.get("overlay_seconds", 8)        # tempo do aviso do Claude por cima da tela
GPU_ALERT = _CFG.get("gpu_alert_percent", 80)
PAGE_SECONDS = _CFG.get("page_seconds", 12)            # tempo de cada página (PC, previsão, música)           # % de GPU que deixa o cartão com borda vermelha

W, H = 480, 320
S = 2                    # supersampling: desenha em 2x e reduz (bordas suaves)

# ----------------------------------------------------------------- visual
BG = (9, 11, 15)
CARD = (17, 21, 27)
TRACK = (32, 38, 48)
LINE = (28, 33, 41)
TEXT = (232, 236, 241)
MUTED = (122, 133, 148)
DIM = (78, 87, 100)
CPU_C = (94, 234, 212)
RAM_C = (165, 180, 252)
GPU_C = (134, 239, 172)
CLAUDE = (217, 119, 87)
OK = (134, 239, 172)
WARN = (251, 191, 36)
HOT = (248, 113, 113)

FONT_DIR = REPO / "res" / "fonts"


def _font(name, size):
    return ImageFont.truetype(str(FONT_DIR / name), int(size * S))


F = {
    "clock": _font("roboto/Roboto-Light.ttf", 42),
    "temp": _font("roboto/Roboto-Light.ttf", 36),
    "date": _font("roboto/Roboto-Regular.ttf", 13),
    "date_b": _font("roboto/Roboto-Medium.ttf", 13),
    "label": _font("roboto/Roboto-Bold.ttf", 11),
    "pct": _font("roboto/Roboto-Medium.ttf", 26),
    "pct_sym": _font("roboto/Roboto-Regular.ttf", 13),
    "small": _font("roboto/Roboto-Regular.ttf", 12),
    "badge": _font("roboto/Roboto-Medium.ttf", 13),
    "row": _font("roboto/Roboto-Medium.ttf", 13),
    "row_m": _font("roboto/Roboto-Regular.ttf", 12),
    "ov_title": _font("roboto/Roboto-Medium.ttf", 13),
    "ov_name": _font("roboto/Roboto-Medium.ttf", 24),
    "ov_msg": _font("roboto/Roboto-Regular.ttf", 15),
    "mini": _font("roboto/Roboto-Bold.ttf", 9),
    "huge": _font("roboto/Roboto-Medium.ttf", 46),
    "huge_sym": _font("roboto/Roboto-Regular.ttf", 18),
    "temp_big": _font("roboto/Roboto-Light.ttf", 64),
    "game": _font("roboto/Roboto-Medium.ttf", 20),
    "game_clock": _font("roboto/Roboto-Light.ttf", 26),
    "fps": _font("roboto/Roboto-Medium.ttf", 54),
    "temp_mid": _font("roboto/Roboto-Light.ttf", 52),
    "ring_pct": _font("roboto/Roboto-Medium.ttf", 30),
}

DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
        "sexta-feira", "sábado", "domingo"]
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]


# ================================================================= dados
class Stats:
    def __init__(self):
        psutil.cpu_percent(None)
        psutil.cpu_percent(None, percpu=True)
        self.nvml = None
        try:
            import pynvml
            pynvml.nvmlInit()
            self.nvml = pynvml
            self.gpu = pynvml.nvmlDeviceGetHandleByIndex(0)
        except Exception:
            pass

    def read(self):
        d = {}
        d["cpu"] = psutil.cpu_percent(None)
        cores = psutil.cpu_percent(None, percpu=True)
        d["cpu_peak"] = max(cores) if cores else 0
        vm = psutil.virtual_memory()
        d["ram"] = vm.percent
        d["ram_used"] = vm.used / 2**30
        d["ram_total"] = vm.total / 2**30
        d["gpu"] = d["gpu_temp"] = None
        if self.nvml:
            try:
                n, h = self.nvml, self.gpu
                d["gpu"] = n.nvmlDeviceGetUtilizationRates(h).gpu
                d["gpu_temp"] = n.nvmlDeviceGetTemperature(h, 0)
                mem = n.nvmlDeviceGetMemoryInfo(h)
                d["vram_used"] = mem.used / 2**30
                d["vram_total"] = mem.total / 2**30
            except Exception:
                pass
        return d


WEATHER_TXT = {
    0: "Céu limpo", 1: "Poucas nuvens", 2: "Parc. nublado", 3: "Nublado",
    45: "Neblina", 48: "Neblina", 51: "Garoa", 53: "Garoa", 55: "Garoa",
    56: "Garoa", 57: "Garoa", 61: "Chuva fraca", 63: "Chuva", 65: "Chuva forte",
    66: "Chuva", 67: "Chuva", 71: "Neve", 73: "Neve", 75: "Neve", 77: "Neve",
    80: "Pancadas", 81: "Pancadas", 82: "Pancadas fortes",
    95: "Tempestade", 96: "Tempestade", 99: "Tempestade",
}


class Weather(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.data = None

    def fetch(self):
        r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=15, params={
            "latitude": LAT, "longitude": LON, "timezone": TIMEZONE,
            "current": "temperature_2m,weather_code,is_day",
            "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max",
            "hourly": "temperature_2m,precipitation_probability", "forecast_days": 5,
        })
        j = r.json()
        c, dd, hh = j["current"], j["daily"], j["hourly"]
        days = [{"date": dd["time"][i], "code": dd["weather_code"][i], "max": dd["temperature_2m_max"][i],
                 "min": dd["temperature_2m_min"][i], "rain": dd["precipitation_probability_max"][i] or 0}
                for i in range(len(dd["time"]))]
        hours = [{"time": hh["time"][i], "temp": hh["temperature_2m"][i],
                  "rain": hh["precipitation_probability"][i] or 0} for i in range(len(hh["time"]))]
        self.data = {
            "temp": c["temperature_2m"], "code": c["weather_code"], "day": c["is_day"],
            "max": dd["temperature_2m_max"][0], "min": dd["temperature_2m_min"][0],
            "days": days, "hours": hours,
        }

    def run(self):
        while True:
            try:
                self.fetch()
                time.sleep(600)
            except Exception:
                time.sleep(60)


class ClaudeUsage(threading.Thread):
    """Tokens desde terça 10h (lidos das conversas locais) + % do limite, se disponível."""

    def __init__(self):
        super().__init__(daemon=True)
        import claude_usage as cu
        self.cu = cu
        self.counter = cu.TokenCounter()
        self.tokens = None
        self.limit = None
        self.pct, self.estimated = None, False
        self.next_fetch = 0.0

    def poll(self):
        self.counter.update()
        self.tokens = self.counter.spent()
        if time.time() >= self.next_fetch:
            try:
                self.cu.fetch_limit(self.tokens)
                self.next_fetch = time.time() + 5 * 60
            except Exception:           # login vencido / sem internet: tenta de novo em 30 min
                self.next_fetch = time.time() + 30 * 60
        self.limit = self.cu.read_limit()
        self.pct, self.estimated = self.cu.estimate(self.limit, self.tokens, self.counter.start.timestamp())

    def run(self):
        while True:
            try:
                self.poll()
            except Exception:
                pass
            time.sleep(60)


def fmt_tokens(n):
    if n >= 1e9:
        return f"{n / 1e9:.1f} B".replace(".", ",")
    if n >= 1e6:
        return f"{n / 1e6:.1f} M".replace(".", ",")
    if n >= 1e3:
        return f"{n / 1e3:.0f} mil"
    return str(n)


def load_events():
    """Último evento de cada sessão, mais recente primeiro."""
    if not EVENTS_FILE.exists():
        return []
    latest = {}
    try:
        lines = EVENTS_FILE.read_text(encoding="utf-8").splitlines()[-300:]
    except Exception:
        return []
    for ln in lines:
        try:
            e = json.loads(ln)
        except Exception:
            continue
        latest[e.get("session") or e.get("ts")] = e
    now = time.time()
    evs = [e for e in latest.values() if now - e.get("ts", 0) < EVENT_MAX_AGE_H * 3600]
    return sorted(evs, key=lambda e: e["ts"], reverse=True)


# ================================================================= desenho
def s(*v):
    return [int(round(x * S)) for x in v]


def text(d, xy, t, f, fill, anchor="la"):
    d.text(s(*xy), t, font=F[f], fill=fill, anchor=anchor)


def tw(t, f):
    return F[f].getlength(t) / S


def fit(t, f, maxw):
    if tw(t, f) <= maxw:
        return t
    while t and tw(t + "…", f) > maxw:
        t = t[:-1]
    return t.rstrip() + "…"


def temp_color(t):
    return OK if t < 60 else WARN if t < 78 else HOT


def gauge(d, cx, cy, r, w, frac, color):
    box = s(cx - r, cy - r, cx + r, cy + r)
    a0, sweep = 135, 270
    d.arc(box, a0, a0 + sweep, fill=TRACK, width=int(w * S))
    rm = r - w / 2
    for a in (a0, a0 + sweep):  # pontas arredondadas do trilho
        x, y = cx + rm * math.cos(math.radians(a)), cy + rm * math.sin(math.radians(a))
        d.ellipse(s(x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=TRACK)
    frac = max(0.0, min(1.0, frac))
    if frac > 0.004:
        a1 = a0 + sweep * frac
        d.arc(box, a0, a1, fill=color, width=int(w * S))
        for a in (a0, a1):
            x, y = cx + rm * math.cos(math.radians(a)), cy + rm * math.sin(math.radians(a))
            d.ellipse(s(x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=color)


def weather_icon(d, cx, cy, code, day):
    sun_c, cloud_c, rain_c = (253, 224, 71), (203, 213, 225), (125, 211, 252)
    cloudy = code not in (0,)
    clear_part = code in (0, 1, 2)

    def sun(x, y, r):
        if day:
            for i in range(8):
                a = math.radians(i * 45)
                d.line(s(x + math.cos(a) * r * 1.45, y + math.sin(a) * r * 1.45,
                         x + math.cos(a) * r * 1.9, y + math.sin(a) * r * 1.9),
                       fill=sun_c, width=int(2 * S))
            d.ellipse(s(x - r, y - r, x + r, y + r), fill=sun_c)
        else:  # lua
            d.ellipse(s(x - r * 1.2, y - r * 1.2, x + r * 1.2, y + r * 1.2), fill=(226, 232, 240))
            d.ellipse(s(x - r * 0.6, y - r * 1.5, x + r * 1.6, y + r * 0.7), fill=BG)

    def cloud(x, y, k=1.0, c=cloud_c):
        d.ellipse(s(x - 13 * k, y - 4 * k, x - 1 * k, y + 8 * k), fill=c)
        d.ellipse(s(x - 7 * k, y - 11 * k, x + 7 * k, y + 3 * k), fill=c)
        d.ellipse(s(x + 1 * k, y - 5 * k, x + 13 * k, y + 7 * k), fill=c)
        d.rounded_rectangle(s(x - 13 * k, y + 1 * k, x + 13 * k, y + 8 * k), radius=int(3 * S), fill=c)

    if code == 0:
        sun(cx, cy, 7)
        return
    if clear_part:
        sun(cx - 5, cy - 5, 5.5)
        cloud(cx + 3, cy + 2, 0.85)
        return
    if code in (45, 48):
        for i, wl in enumerate((22, 26, 18)):
            d.line(s(cx - wl / 2, cy - 7 + i * 7, cx + wl / 2, cy - 7 + i * 7), fill=cloud_c, width=int(3 * S))
        return
    cloud(cx, cy - 3)
    if code >= 51 and code < 95:
        for i in (-7, 0, 7):
            d.line(s(cx + i, cy + 9, cx + i - 3, cy + 15), fill=rain_c, width=int(2 * S))
    if code >= 95:
        d.polygon(s(cx + 1, cy + 6, cx - 5, cy + 14, cx, cy + 14, cx - 3, cy + 20, cx + 5, cy + 11, cx, cy + 11),
                  fill=(253, 224, 71))


def claude_mark(d, cx, cy, r, color):
    for i in range(6):
        a = math.radians(i * 30 + 90)
        d.line(s(cx - math.cos(a) * r, cy - math.sin(a) * r, cx + math.cos(a) * r, cy + math.sin(a) * r),
               fill=color, width=int(2.4 * S))


def wrap(t, f, maxw, lines=2):
    words, out, cur = t.split(), [], ""
    for w in words:
        nxt = (cur + " " + w).strip()
        if tw(nxt, f) <= maxw:
            cur = nxt
        else:
            out.append(cur)
            cur = w
            if len(out) == lines:
                break
    if len(out) < lines and cur:
        out.append(cur)
    if len(out) == lines and " ".join(out) != " ".join(words):
        out[-1] = fit(out[-1] + " …", f, maxw)
    return [fit(l, f, maxw) for l in out if l]


def draw_overlay(img, e):
    """Aviso grande do Claude por cima de tudo."""
    dark = Image.new("RGB", img.size, BG)
    img = Image.blend(img, dark, 0.72)
    d = ImageDraw.Draw(img)
    waiting = e.get("type") == "waiting"
    col = WARN if waiting else OK
    x0, y0, x1, y1 = 40, 58, 440, 262
    d.rounded_rectangle(s(x0, y0, x1, y1), radius=int(16 * S), fill=CARD, outline=col, width=int(2 * S))
    claude_mark(d, x0 + 30, y0 + 32, 10, CLAUDE)
    text(d, (x0 + 50, y0 + 32), "Claude", "row", TEXT, "lm")
    badge = "Precisa de você" if waiting else "Sessão terminou"
    bw = tw(badge, "ov_title") + 22
    d.rounded_rectangle(s(x1 - 20 - bw, y0 + 20, x1 - 20, y0 + 44), radius=int(12 * S),
                        fill=tuple(int(c * 0.22) for c in col))
    text(d, (x1 - 20 - bw / 2, y0 + 32), badge, "ov_title", col, "mm")
    text(d, (x0 + 24, y0 + 84), fit(e.get("project", "sessão"), "ov_name", x1 - x0 - 48), "ov_name", TEXT, "lm")
    msg = e.get("message") or ("terminou" if not waiting else "esperando você")
    for i, ln in enumerate(wrap(msg, "ov_msg", x1 - x0 - 48)):
        text(d, (x0 + 24, y0 + 122 + i * 22), ln, "ov_msg", MUTED, "lm")
    text(d, (x0 + 24, y1 - 22), dt.datetime.fromtimestamp(e["ts"]).strftime("%H:%M"), "small", DIM, "lm")
    return img


def ago(ts):
    sec = max(0, time.time() - ts)
    if sec < 60:
        return "agora"
    if sec < 3600:
        return f"{int(sec // 60)} min"
    return f"{int(sec // 3600)} h"


DIAS_CURTOS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
RAIN_C = (125, 211, 252)
LINE_C = (253, 186, 116)


def body_forecast(d, weather, now):
    """Página de previsão: próximas 12 horas (a cada 2 h) + 5 dias. Números grandes pra ler de longe."""
    if not weather or "days" not in weather:
        text(d, (240, 185), "Carregando previsão…", "small", DIM, "mm")
        return
    # próximas horas
    y0, y1 = 64, 158
    d.rounded_rectangle(s(18, y0, 462, y1), radius=int(12 * S), fill=CARD)
    key = now.strftime("%Y-%m-%dT%H:00")
    hrs = weather["hours"]
    i0 = next((i for i, h in enumerate(hrs) if h["time"] >= key), 0)
    hrs = hrs[i0:i0 + 12:2]                      # 6 pontos: agora, +2h, +4h...
    if len(hrs) >= 2:
        temps = [h["temp"] for h in hrs]
        lo, hi = min(temps), max(temps)
        span = max(hi - lo, 1)
        xs = [52 + i * (376 / (len(hrs) - 1)) for i in range(len(hrs))]
        ys = [y0 + 58 - (t - lo) / span * 22 for t in temps]
        d.line([v for xy in zip(xs, ys) for v in s(*xy)], fill=LINE_C, width=int(3 * S), joint="curve")
        for i, (x, y) in enumerate(zip(xs, ys)):
            d.ellipse(s(x - 3.5, y - 3.5, x + 3.5, y + 3.5), fill=LINE_C)
            text(d, (x, y - 15), f"{round(temps[i])}°", "badge", TEXT, "mm")
            hour = "agora" if i == 0 else hrs[i]["time"][11:13] + "h"
            text(d, (x, y1 - 13), hour, "small", MUTED, "mm")
            if hrs[i]["rain"] >= 30:
                text(d, (x, y1 - 29), f"{hrs[i]['rain']}%", "small", RAIN_C, "mm")
    # 5 dias
    days = weather["days"][:5]
    top, bot, gap = 166, 310, 8.5
    cw = (444 - gap * (len(days) - 1)) / len(days)
    for i, dd in enumerate(days):
        x0 = 18 + i * (cw + gap)
        cx = x0 + cw / 2
        d.rounded_rectangle(s(x0, top, x0 + cw, bot), radius=int(12 * S), fill=CARD)
        date = dt.date.fromisoformat(dd["date"])
        label = "Hoje" if i == 0 else DIAS_CURTOS[date.weekday()]
        text(d, (cx, top + 15), label, "row", TEXT if i == 0 else MUTED, "mm")
        weather_icon(d, cx, top + 44, dd["code"], 1)
        text(d, (cx, top + 82), f"{round(dd['max'])}°", "ov_name", TEXT, "mm")
        text(d, (cx, top + 106), f"{round(dd['min'])}°", "ov_msg", MUTED, "mm")
        rain = dd["rain"]
        rc = RAIN_C if rain >= 30 else DIM
        rt = f"{rain}%"
        wx = tw(rt, "badge")
        dx = cx - (wx + 10) / 2                 # gotinha + %
        d.ellipse(s(dx, top + 128, dx + 7, top + 135), fill=rc)
        d.polygon(s(dx + 0.6, top + 130, dx + 3.5, top + 123, dx + 6.4, top + 130), fill=rc)
        text(d, (dx + 10, top + 130), rt, "badge", rc, "lm")


def fmt_time(sec):
    sec = int(sec or 0)
    return f"{sec // 60}:{sec % 60:02d}"


def body_music(img, d, m):
    """Página de música: capa, título, artista e progresso."""
    y0, y1 = 64, 310
    d.rounded_rectangle(s(18, y0, 462, y1), radius=int(14 * S), fill=CARD)
    if not m:
        text(d, (240, 187), "Nada tocando", "small", DIM, "mm")
        return
    ax0, ay0, a = 32, 80, 214
    if m.get("art") is not None:
        art = m["art"].copy()
        w, h = art.size
        side = min(w, h)                                  # corta pro quadrado
        art = art.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
        art = art.resize((a * S, a * S), Image.LANCZOS)
        mask = Image.new("L", art.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, a * S - 1, a * S - 1), radius=int(10 * S), fill=255)
        img.paste(art, (ax0 * S, ay0 * S), mask)
    else:
        d.rounded_rectangle(s(ax0, ay0, ax0 + a, ay0 + a), radius=int(10 * S), fill=TRACK)
        cx, cy = ax0 + a / 2, ay0 + a / 2
        d.ellipse(s(cx - 22, cy + 10, cx - 2, cy + 26), fill=DIM)          # nota musical
        d.rectangle(s(cx - 6, cy - 30, cx - 2, cy + 18), fill=DIM)
        d.polygon(s(cx - 6, cy - 30, cx + 20, cy - 22, cx + 20, cy - 12, cx - 2, cy - 19), fill=DIM)

    x0, x1 = ax0 + a + 18, 448
    # app + estado
    app_c = OK if m["app"] == "Spotify" else MUTED
    if m["playing"]:
        d.polygon(s(x0, ay0 + 6, x0, ay0 + 16, x0 + 9, ay0 + 11), fill=app_c)
        state = "tocando"
    else:
        d.rectangle(s(x0, ay0 + 6, x0 + 3, ay0 + 16), fill=MUTED)
        d.rectangle(s(x0 + 6, ay0 + 6, x0 + 9, ay0 + 16), fill=MUTED)
        state = "pausado"
    text(d, (x0 + 15, ay0 + 11), f"{m['app']} · {state}", "small", app_c if m["playing"] else MUTED, "lm")
    # título (até 2 linhas), artista, álbum
    lines = wrap(m["title"], "ov_name", x1 - x0)
    for i, ln in enumerate(lines):
        text(d, (x0, ay0 + 44 + i * 28), ln, "ov_name", TEXT, "lm")
    ya = ay0 + 44 + len(lines) * 28 + 4
    text(d, (x0, ya), fit(m["artist"], "ov_msg", x1 - x0), "ov_msg", MUTED, "lm")
    if m["album"] and m["album"] != m["title"]:
        text(d, (x0, ya + 20), fit(m["album"], "small", x1 - x0), "small", DIM, "lm")
    # progresso
    if m.get("dur"):
        by = ay0 + a - 22
        bar(d, x0, by, x1, 5, (m["pos"] or 0) / m["dur"], OK if m["app"] == "Spotify" else CLAUDE)
        text(d, (x0, by + 17), fmt_time(m["pos"]), "small", MUTED, "lm")
        text(d, (x1, by + 17), fmt_time(m["dur"]), "small", MUTED, "rm")


def page_dots(d, pages, page):
    if not pages or len(pages) < 2:
        return
    n = len(pages)
    x = 240 - (n - 1) * 5
    for i, pg in enumerate(pages):
        r = 2
        d.ellipse(s(x + i * 10 - r, 315 - r, x + i * 10 + r, 315 + r), fill=TEXT if pg == page else DIM)


def render(st, weather, events, usage=None, overlay=None, now=None, page="pc", music=None, pages=None):
    now = now or dt.datetime.now()
    img = Image.new("RGB", (W * S, H * S), BG)
    d = ImageDraw.Draw(img)

    # ---------------------------------------------------------- cabeçalho
    clock = now.strftime("%H:%M")
    text(d, (18, 31), clock, "clock", TEXT, "lm")
    x = 18 + tw(clock, "clock") + 14
    text(d, (x, 22), DIAS[now.weekday()], "date", MUTED, "lm")
    date_txt = f"{now.day} de {MESES[now.month - 1]}"
    text(d, (x, 40), date_txt, "date_b", TEXT, "lm")
    date_end = x + max(tw(DIAS[now.weekday()], "date"), tw(date_txt, "date_b")) + 12

    if weather:
        t = f"{round(weather['temp'])}°"
        text(d, (462, 31), t, "temp", TEXT, "rm")
        ix = 462 - tw(t, "temp") - 22
        weather_icon(d, ix, 30, weather["code"], weather["day"])
        rx = ix - 24
        text(d, (rx, 22), fit(CITY, "date_b", rx - date_end), "date_b", TEXT, "rm")
        cond = WEATHER_TXT.get(weather["code"], "")
        temps = f"{round(weather['max'])}°/{round(weather['min'])}°"
        desc = f"{cond} · {temps}" if tw(f"{cond} · {temps}", "date") <= rx - date_end else temps
        text(d, (rx, 40), desc, "date", MUTED, "rm")
    else:
        text(d, (462, 31), f"{CITY} · …", "date", DIM, "rm")

    if page in ("forecast", "music"):
        if page == "forecast":
            body_forecast(d, weather, now)
        else:
            body_music(img, d, music)
        page_dots(d, pages, page)
        if overlay:
            img = draw_overlay(img, overlay)
        return img.resize((W, H), Image.LANCZOS)

    # ---------------------------------------------------------- cards
    cw, ch, gap, top = 142, 146, 9, 64
    cards = [
        ("CPU", st["cpu"], CPU_C, None, f"pico núcleo {st['cpu_peak']:.0f}%"),
        ("RAM", st["ram"], RAM_C, None, f"{st['ram_used']:.1f} / {st['ram_total']:.0f} GB"),
        ("GPU", st["gpu"], GPU_C, st["gpu_temp"],
         f"VRAM {st.get('vram_used', 0):.1f} / {st.get('vram_total', 0):.0f} GB" if st["gpu"] is not None else "sem leitura"),
    ]
    for i, (label, val, color, temp, sub) in enumerate(cards):
        x0 = 18 + i * (cw + gap)
        alert = label == "GPU" and val is not None and val >= GPU_ALERT
        d.rounded_rectangle(s(x0, top, x0 + cw, top + ch), radius=int(12 * S), fill=CARD,
                            outline=HOT if alert else None, width=int(2 * S) if alert else 0)
        d.ellipse(s(x0 + 12, top + 13, x0 + 18, top + 19), fill=color)
        text(d, (x0 + 24, top + 16), label, "label", MUTED, "lm")
        if temp is not None:
            text(d, (x0 + cw - 12, top + 16), f"{temp:.0f}°C", "badge", temp_color(temp), "rm")
        cx, cy = x0 + cw / 2, top + 76
        gauge(d, cx, cy, 42, 7, (val or 0) / 100, color)
        num = "–" if val is None else f"{val:.0f}"
        wn, wp = tw(num, "pct"), tw("%", "pct_sym")
        lx = cx - (wn + wp + 1) / 2
        text(d, (lx, cy + 9), num, "pct", TEXT, "ls")
        text(d, (lx + wn + 1, cy + 9), "%", "pct_sym", MUTED, "ls")
        text(d, (cx, top + 128), fit(sub, "small", cw - 16), "small", MUTED, "mm")

    # ---------------------------------------------------------- Claude
    py0, py1 = 220, 310
    px1 = 350                     # painel de notificações | quadrado do uso à direita
    fresh = events and time.time() - events[0]["ts"] < 20
    d.rounded_rectangle(s(18, py0, px1, py1), radius=int(12 * S), fill=CARD,
                        outline=CLAUDE if fresh else None, width=int(1.5 * S) if fresh else 0)
    claude_mark(d, 32, py0 + 16, 6, CLAUDE)
    text(d, (43, py0 + 16), "Claude", "row", TEXT, "lm")
    waiting = sum(1 for e in events if e.get("type") == "waiting")
    status = (f"· {waiting} esperando você" if waiting else
              "· tudo em dia" if events else "· sem notificações")
    text(d, (43 + tw("Claude", "row") + 6, py0 + 16), status, "small", WARN if waiting else DIM, "lm")

    rx1 = px1 - 12
    rows = events[:2]
    if not rows:
        text(d, ((18 + px1) / 2, py0 + 56), "Quando uma sessão terminar, aparece aqui", "small", DIM, "mm")
    for j, e in enumerate(rows):
        ry = py0 + 45 + j * 28
        if j:
            d.line(s(30, ry - 14, rx1, ry - 14), fill=LINE, width=int(1 * S))
        col = WARN if e.get("type") == "waiting" else OK
        d.ellipse(s(30, ry - 3.5, 37, ry + 3.5), fill=col)
        when = ago(e["ts"])
        text(d, (rx1, ry), when, "small", MUTED, "rm")
        name = fit(e.get("project", "sessão"), "row", 110)
        text(d, (45, ry), name, "row", TEXT, "lm")
        nx = 45 + tw(name, "row") + 8
        msg = e.get("message") or ("terminou" if e.get("type") == "done" else "esperando você")
        text(d, (nx, ry), fit(msg, "row_m", rx1 - tw(when, "small") - 10 - nx), "row_m", MUTED, "lm")

    # ---------------------------------------------------------- quadrado do uso do Claude
    ux0, ux1 = px1 + 8, 462
    ucx = (ux0 + ux1) / 2
    d.rounded_rectangle(s(ux0, py0, ux1, py1), radius=int(12 * S), fill=CARD)
    text(d, (ucx, py0 + 13), "LIMITE SEMANAL", "mini", MUTED, "mm")
    pct = usage.pct if usage else None
    est = usage.estimated if usage else False
    ring_c = DIM if pct is None else HOT if pct >= 90 else WARN if pct >= 70 else CLAUDE
    gcy = py0 + 47
    gauge(d, ucx, gcy, 25, 5, (pct or 0) / 100, ring_c)
    if pct is None:
        text(d, (ucx, gcy + 1), "–", "badge", DIM, "mm")
    else:
        num = f"{'≈' if est else ''}{pct:.0f}"
        wn, wp = tw(num, "badge"), tw("%", "mini")
        lx = ucx - (wn + wp + 1) / 2
        text(d, (lx, gcy + 6), num, "badge", TEXT, "ls")
        text(d, (lx + wn + 1, gcy + 6), "%", "mini", MUTED, "ls")
    tok = "…" if not usage or usage.tokens is None else f"{fmt_tokens(usage.tokens)} tokens"
    text(d, (ucx, py1 - 13), tok, "row_m", TEXT, "mm")
    page_dots(d, pages, page)

    if overlay:
        img = draw_overlay(img, overlay)
    return img.resize((W, H), Image.LANCZOS)


def bar(d, x0, y, x1, h, frac, color):
    d.rounded_rectangle(s(x0, y, x1, y + h), radius=int(h / 2 * S), fill=TRACK)
    frac = max(0.0, min(1.0, frac))
    if frac > 0.01:
        d.rounded_rectangle(s(x0, y, x0 + max(h, (x1 - x0) * frac), y + h), radius=int(h / 2 * S), fill=color)


def render_game(st, game, usage=None, overlay=None, now=None, fps=None):
    """Modo jogo: GPU e temperatura em destaque."""
    now = now or dt.datetime.now()
    img = Image.new("RGB", (W * S, H * S), BG)
    d = ImageDraw.Draw(img)

    # ---------------------------------------------------------- cabeçalho
    pill = "MODO JOGO"
    pw = tw(pill, "mini") + 16
    d.rounded_rectangle(s(18, 9, 18 + pw, 25), radius=int(8 * S), fill=tuple(int(c * 0.2) for c in GPU_C))
    text(d, (18 + pw / 2, 17), pill, "mini", GPU_C, "mm")
    text(d, (18, 41), fit(game["name"], "game", 300), "game", TEXT, "lm")
    text(d, (462, 22), now.strftime("%H:%M"), "game_clock", TEXT, "rm")
    mins = int((time.time() - game["since"]) // 60)
    played = f"jogando há {mins} min" if mins < 60 else f"jogando há {mins // 60}h{mins % 60:02d}"
    text(d, (462, 45), played, "small", MUTED, "rm")

    # ---------------------------------------------------------- FPS | GPU | temperatura
    top, bot, cw, gap = 60, 226, 142, 9
    xs = [18 + i * (cw + gap) for i in range(3)]

    # FPS
    x0 = xs[0]
    cx = x0 + cw / 2
    d.rounded_rectangle(s(x0, top, x0 + cw, bot), radius=int(14 * S), fill=CARD)
    text(d, (x0 + 12, top + 16), "FPS", "label", MUTED, "lm")
    f, low, err = (fps or (None, None, None))
    if f is None:
        text(d, (cx, top + 80), "–", "fps", DIM, "mm")
        text(d, (cx, top + 132), err or "esperando quadros…", "small", DIM, "mm")
    else:
        fc = OK if f >= 60 else WARN if f >= 30 else HOT
        text(d, (cx, top + 80), f"{f:.0f}", "fps", fc, "mm")
        text(d, (cx, top + 132), f"1% low {low:.0f}", "small", MUTED, "mm")

    # GPU
    x0 = xs[1]
    cx = x0 + cw / 2
    gpu = st["gpu"]
    alert = gpu is not None and gpu >= GPU_ALERT
    d.rounded_rectangle(s(x0, top, x0 + cw, bot), radius=int(14 * S), fill=CARD,
                        outline=HOT if alert else None, width=int(2 * S) if alert else 0)
    d.ellipse(s(x0 + 12, top + 13, x0 + 18, top + 19), fill=GPU_C)
    text(d, (x0 + 24, top + 16), "GPU", "label", MUTED, "lm")
    cy = top + 88
    gauge(d, cx, cy, 50, 9, (gpu or 0) / 100, GPU_C)
    num = "–" if gpu is None else f"{gpu:.0f}"
    wn, wp = tw(num, "ring_pct"), tw("%", "pct_sym")
    lx = cx - (wn + wp + 1) / 2
    text(d, (lx, cy + 11), num, "ring_pct", TEXT, "ls")
    text(d, (lx + wn + 1, cy + 11), "%", "pct_sym", MUTED, "ls")

    # temperatura
    x0 = xs[2]
    cx = x0 + cw / 2
    d.rounded_rectangle(s(x0, top, x0 + cw, bot), radius=int(14 * S), fill=CARD)
    text(d, (x0 + 12, top + 16), "TEMP. GPU", "label", MUTED, "lm")
    t = st["gpu_temp"]
    tc = temp_color(t) if t is not None else DIM
    text(d, (cx, top + 74), "–" if t is None else f"{t:.0f}°", "temp_mid", tc, "mm")
    bar(d, x0 + 14, top + 112, x0 + cw - 14, 6, ((t or 30) - 30) / 60, tc)
    text(d, (x0 + 14, top + 126), "30°", "mini", DIM, "lm")
    text(d, (x0 + cw - 14, top + 126), "90°", "mini", DIM, "rm")
    if gpu is not None:
        text(d, (cx, top + 148), f"VRAM {st.get('vram_used', 0):.1f}/{st.get('vram_total', 0):.0f} GB",
             "small", MUTED, "mm")

    # ---------------------------------------------------------- linha de baixo
    cw, gap, y0, y1 = 142, 9, 234, 310
    pct = usage.pct if usage else None
    est = usage.estimated if usage else False
    minis = [
        ("CPU", st["cpu"], CPU_C, f"{st['cpu']:.0f}%"),
        ("RAM", st["ram"], RAM_C, f"{st['ram_used']:.1f} GB"),
        ("CLAUDE · SEMANA", pct, CLAUDE, "–" if pct is None else f"{'≈' if est else ''}{pct:.0f}%"),
    ]
    for i, (label, val, color, txt) in enumerate(minis):
        x0 = 18 + i * (cw + gap)
        d.rounded_rectangle(s(x0, y0, x0 + cw, y1), radius=int(12 * S), fill=CARD)
        text(d, (x0 + 12, y0 + 15), label, "mini", MUTED, "lm")
        text(d, (x0 + 12, y0 + 41), txt, "pct", TEXT, "lm")
        bar(d, x0 + 12, y0 + 60, x0 + cw - 12, 5, (val or 0) / 100, color)

    if overlay:
        img = draw_overlay(img, overlay)
    return img.resize((W, H), Image.LANCZOS)


# ================================================================= tela
REGIONS =[(0, 0, W, 60), (0, 60, 160, 214), (160, 60, 320, 214), (320, 60, W, 214), (0, 214, W, H)]


FULL_REFRESH_SECONDS = 60   # redesenha a tela inteira de tempos em tempos (limpa qualquer resto)


def open_lcd():
    from library.lcd.lcd_comm_rev_a import LcdCommRevA, Orientation

    class SafeLcd(LcdCommRevA):
        # O driver só esvazia o buffer da serial a cada linha no macOS ("bitmap corruption on the
        # display"). No Windows, sem isso, bytes podem chegar atrasados/fora de ordem e virar pixels errados.
        def WriteLine(self, line):
            self.serial_write(line)
            self.lcd_serial.flush()

    lcd = SafeLcd(com_port=COM_PORT, display_width=320, display_height=480)
    lcd.Reset()
    lcd.InitializeComm()
    lcd.SetBrightness(level=BRIGHTNESS)
    lcd.SetOrientation(orientation=Orientation.LANDSCAPE)
    return lcd


def push(lcd, frame, prev):
    """Manda só os retângulos que mudaram desde o último quadro."""
    cur = np.asarray(frame)
    if prev is None:
        lcd.DisplayPILImage(frame, 0, 0)
        return cur
    diff = np.any(cur != prev, axis=2)
    for x0, y0, x1, y1 in REGIONS:
        sub = diff[y0:y1, x0:x1]
        if not sub.any():
            continue
        ys, xs = np.where(sub)
        # margem de 2 px e alinhado em múltiplos de 4: evita retângulos minúsculos/ímpares
        bx0 = max(0, (x0 + xs.min() - 2) // 4 * 4)
        by0 = max(0, (y0 + ys.min() - 2) // 2 * 2)
        bx1 = min(W, -(-(x0 + xs.max() + 3) // 4) * 4)
        by1 = min(H, -(-(y0 + ys.max() + 3) // 2) * 2)
        lcd.DisplayPILImage(frame.crop((bx0, by0, bx1, by1)), int(bx0), int(by0))
    return cur


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", help="salva uma prévia PNG e sai")
    ap.add_argument("--overlay", action="store_true", help="prévia com o aviso do Claude por cima")
    ap.add_argument("--gpu", type=float, help="prévia com uso de GPU forçado (ex.: 92)")
    ap.add_argument("--game", help="prévia do modo jogo com esse nome de jogo")
    ap.add_argument("--demo", action="store_true", help="prévia só com dados de exemplo (para prints)")
    ap.add_argument("--page", default="pc", choices=["pc", "forecast", "music"], help="página da prévia")
    args = ap.parse_args()

    try:
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass

    stats = Stats()
    weather = Weather()
    usage = ClaudeUsage()

    if args.preview:
        try:
            weather.fetch()
        except Exception:
            pass
        usage.poll()
        time.sleep(1)
        st = stats.read()
        if args.gpu is not None:
            st["gpu"] = args.gpu
        evs = load_events()
        if args.demo:
            from types import SimpleNamespace
            global CITY
            CITY = "São Paulo"
            st = {"cpu": 23, "cpu_peak": 61, "ram": 41, "ram_used": 13.1, "ram_total": 32,
                  "gpu": args.gpu if args.gpu is not None else 47, "gpu_temp": 58,
                  "vram_used": 5.4, "vram_total": 12}
            import math as _m
            t0 = dt.datetime.now().replace(minute=0, second=0, microsecond=0)
            hours = [{"time": (t0 + dt.timedelta(hours=i)).strftime("%Y-%m-%dT%H:00"),
                      "temp": 23 + 5 * _m.sin((i - 2) / 4), "rain": [0, 0, 10, 40, 60, 30][i % 6]}
                     for i in range(24)]
            days = [{"date": (t0.date() + dt.timedelta(days=i)).isoformat(), "code": c, "max": mx,
                     "min": mn, "rain": r}
                    for i, (c, mx, mn, r) in enumerate([(2, 29, 17, 10), (61, 24, 16, 70), (3, 22, 15, 30),
                                                         (0, 27, 14, 0), (1, 30, 18, 5)])]
            weather.data = {"temp": 24, "code": 2, "day": 1, "max": 29, "min": 17, "days": days, "hours": hours}
            now_ts = time.time()
            evs = [{"ts": now_ts - 5, "session": "a", "type": "done", "project": "meu-site",
                    "message": "Deploy feito e testes passando"},
                   {"ts": now_ts - 420, "session": "b", "type": "waiting", "project": "app-mobile",
                    "message": "precisa da sua permissão"}]
            usage = SimpleNamespace(pct=37, estimated=False, tokens=3_400_000)
        ov = evs[0] if args.overlay and evs else None
        if args.game:
            if st["gpu_temp"] is not None and args.gpu is not None and not args.demo:
                st["gpu_temp"] = 30 + args.gpu * 0.4
            img = render_game(st, {"name": args.game, "since": time.time() - 4500}, usage, ov,
                              fps=(144.0, 97.0, None))
        else:
            music = None
            if args.page == "music":
                art = Image.new("RGB", (300, 300))
                ad = ImageDraw.Draw(art)
                for y in range(300):            # capa de exemplo: degradê
                    ad.line((0, y, 300, y), fill=(40 + y // 3, 30 + y // 6, 90 + y // 4))
                ad.ellipse((90, 90, 210, 210), outline=(250, 200, 120), width=10)
                music = {"title": "Midnight Drive", "artist": "Neon Garage", "album": "Night Runs",
                         "playing": True, "pos": 83, "dur": 214, "art": art, "app": "Spotify"}
            img = render(st, weather.data, evs, usage, overlay=ov, page=args.page, music=music,
                         pages=["pc", "forecast", "music"])
        img.save(args.preview)
        img.resize((W * 2, H * 2), Image.NEAREST).save(args.preview.replace(".png", "@2x.png"))
        print("ok", args.preview)
        return

    weather.start()
    from music_reader import MusicReader
    music = MusicReader()
    music.start()
    page_i, page_t = 0, time.time()
    usage.start()
    stop = threading.Event()
    last_seen = time.time()          # só avisa eventos que chegarem depois de ligar
    overlay, overlay_until = None, 0.0
    last_full = time.time()
    from game_detect import GameDetector
    detector, next_detect, game = GameDetector(), 0.0, None
    from fps_reader import FpsReader
    fps_reader = FpsReader()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    lcd, prev = None, None
    while not stop.is_set():
        t0 = time.time()
        try:
            if lcd is None:
                lcd, prev = open_lcd(), None
            events = load_events()
            new = [e for e in events if e["ts"] > last_seen]
            if new:
                last_seen = max(e["ts"] for e in new)
                overlay, overlay_until = new[0], time.time() + OVERLAY_SECONDS
            if overlay and time.time() > overlay_until:
                overlay = None
            st = stats.read()
            if time.time() >= next_detect:          # olha a janela ativa a cada 2 s
                try:
                    game = detector.update(st["gpu"])
                except Exception:
                    game = None
                next_detect = time.time() + 2
                fps_reader.watch(game["pid"] if game else None)
            if game:
                f, low = fps_reader.read()
                frame = render_game(st, game, usage, overlay, fps=(f, low, fps_reader.error))
            else:
                pages = ["pc", "forecast"] + (["music"] if music.data else [])
                if time.time() - page_t >= PAGE_SECONDS:
                    page_i, page_t = page_i + 1, time.time()
                page = pages[page_i % len(pages)]
                frame = render(st, weather.data, events, usage, overlay, page=page,
                               music=music.data, pages=pages)
            if time.time() - last_full > FULL_REFRESH_SECONDS:
                prev, last_full = None, time.time()   # força quadro inteiro
            prev = push(lcd, frame, prev)
        except (Exception, SystemExit) as ex:  # tela desconectada / porta ocupada / PC voltou do sono
            print("erro na tela:", ex, "- tentando de novo em 5s", flush=True)
            try:
                lcd.closeSerial()
            except Exception:
                pass
            lcd = None
            stop.wait(5)
            continue
        stop.wait(max(0.2, 1.0 - (time.time() - t0)))

    fps_reader.stop()
    if lcd:
        try:
            lcd.DisplayPILImage(Image.new("RGB", (W, H), (0, 0, 0)), 0, 0)
            lcd.closeSerial()
        except Exception:
            pass


if __name__ == "__main__":
    main()
