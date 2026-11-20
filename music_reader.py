"""
"Tocando agora" pelo controle de mídia do Windows (Spotify, YouTube no navegador, players...).
Roda numa thread própria e guarda o último estado em `MusicReader.data`.
"""
import asyncio
import datetime as dt
import io
import threading
import time

from PIL import Image

APPS = {"spotify": "Spotify", "chrome": "Chrome", "opera": "Opera GX", "msedge": "Edge",
        "firefox": "Firefox", "zunemusic": "Media Player", "microsoft.media": "Media Player",
        "deezer": "Deezer", "tidal": "Tidal", "youtube": "YouTube Music"}
PAUSED_KEEP_SECONDS = 120     # depois de pausar, a página de música continua por 2 min


def app_name(aumid):
    low = (aumid or "").lower()
    for k, v in APPS.items():
        if k in low:
            return v
    return (aumid or "").split("!")[0].split(".")[-1] or "Mídia"


async def _thumb(ref):
    from winrt.windows.storage.streams import Buffer, DataReader, InputStreamOptions
    stream = await ref.open_read_async()
    buf = Buffer(stream.size)
    await stream.read_async(buf, buf.capacity, InputStreamOptions.READ_AHEAD)
    try:
        raw = bytes(buf)
    except TypeError:
        reader = DataReader.from_buffer(buf)
        raw = bytearray(buf.length)
        reader.read_bytes(raw)
    return Image.open(io.BytesIO(bytes(raw))).convert("RGB")


class MusicReader(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.data = None          # dict ou None
        self._art_key, self._art = None, None
        self._last_playing = 0.0

    async def _poll(self, mgr):
        s = mgr.get_current_session()
        if s is None:
            return None
        p = await s.try_get_media_properties_async()
        if not p or not (p.title or "").strip():
            return None
        playing = s.get_playback_info().playback_status == 4   # 4 = Playing, 5 = Paused
        if playing:
            self._last_playing = time.time()
        elif time.time() - self._last_playing > PAUSED_KEEP_SECONDS:
            return None
        key = (p.title, p.artist)
        if key != self._art_key:
            self._art_key, self._art = key, None
            if p.thumbnail is not None:
                try:
                    self._art = await _thumb(p.thumbnail)
                except Exception:
                    self._art = None
        t = s.get_timeline_properties()
        pos, end = t.position.total_seconds(), t.end_time.total_seconds()
        if playing and t.last_updated_time:
            try:
                pos += (dt.datetime.now(dt.timezone.utc) - t.last_updated_time).total_seconds()
            except Exception:
                pass
        return {"title": p.title, "artist": p.artist or "", "album": p.album_title or "",
                "playing": playing, "pos": max(0.0, min(pos, end)) if end > 0 else None,
                "dur": end if end > 0 else None, "art": self._art, "app": app_name(s.source_app_user_model_id)}

    async def _loop(self):
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as M
        mgr = await M.request_async()
        while True:
            try:
                self.data = await self._poll(mgr)
            except Exception:
                self.data = None
            await asyncio.sleep(1)

    def run(self):
        while True:
            try:
                asyncio.run(self._loop())
            except Exception:
                time.sleep(10)
