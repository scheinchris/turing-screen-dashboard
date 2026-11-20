"""
Teste de pixels: mostra telas de cor sólida, cada uma enviada inteira de uma vez.
Pixel que continua errado em TODAS as cores, sempre no mesmo lugar = defeito físico do painel.
Pixel que só aparece no painel normal, perto de números que mudam = coisa do programa.
"""
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "turing-smart-screen-python"))
from library.lcd.lcd_comm_rev_a import LcdCommRevA, Orientation  # noqa: E402

W, H = 480, 320
FONT = ImageFont.truetype(str(BASE / "turing-smart-screen-python/res/fonts/roboto/Roboto-Medium.ttf"), 30)

import json  # noqa: E402
try:
    COM = json.loads((BASE / "config.json").read_text(encoding="utf-8")).get("com_port", "AUTO")
except Exception:
    COM = "AUTO"
lcd = LcdCommRevA(com_port=COM, display_width=320, display_height=480)
lcd.Reset()
lcd.InitializeComm()
lcd.SetBrightness(level=30)
lcd.SetOrientation(orientation=Orientation.LANDSCAPE)


def show(color, label, secs):
    img = Image.new("RGB", (W, H), color)
    if label:
        d = ImageDraw.Draw(img)
        d.text((W / 2, H / 2), label, font=FONT, fill=(128, 128, 128), anchor="mm")
    lcd.DisplayPILImage(img, 0, 0)
    time.sleep(secs)


show((0, 0, 0), "Teste de pixels em 5 s", 5)
for color, name in [((255, 255, 255), "branco"), ((0, 0, 0), "preto"), ((255, 0, 0), "vermelho"),
                    ((0, 255, 0), "verde"), ((0, 0, 255), "azul"), ((128, 128, 128), "cinza")]:
    show(color, name, 1.2)   # nome rápido no meio
    show(color, "", 4)       # tela limpa pra procurar os pixels
show((0, 0, 0), "Fim do teste", 3)
lcd.closeSerial()
