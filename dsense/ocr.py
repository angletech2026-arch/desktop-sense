"""Windows 內建 OCR（Windows.Media.Ocr）。完全在本機跑，不送任何雲端。

每個 Ocr 物件綁一個 asyncio loop，只能在建立它的那條執行緒使用。
"""
from __future__ import annotations

import asyncio
import re

from PIL import Image

from .i18n import L

_CJK = "⺀-鿿豈-﫿　-〿＀-￯"
_CJK_GAP = re.compile(f"(?<=[{_CJK}])[ \t]+(?=[{_CJK}])")


def join_cjk(text: str) -> str:
    """Windows OCR 會在每個中文字之間塞空白：「建 置 失 敗」→「建置失敗」。"""
    return _CJK_GAP.sub("", text)


class Ocr:
    def __init__(self, language: str = "zh-Hant-TW") -> None:
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter

        self._SoftwareBitmap = SoftwareBitmap
        self._BGRA8 = BitmapPixelFormat.BGRA8
        self._DataWriter = DataWriter
        engine = None
        try:
            engine = OcrEngine.try_create_from_language(Language(language))
        except OSError:
            engine = None
        self.engine = engine or OcrEngine.try_create_from_user_profile_languages()
        if self.engine is None:
            raise RuntimeError(L(f"找不到可用的 Windows OCR 語言（{language}）",
                                 f"no usable Windows OCR language found ({language})"))
        self.max_dim = int(OcrEngine.max_image_dimension)
        self.loop = asyncio.new_event_loop()

    def recognize(self, img: Image.Image) -> list[str]:
        img = img.convert("RGBA")
        if max(img.size) > self.max_dim:
            img.thumbnail((self.max_dim, self.max_dim))
        writer = self._DataWriter()
        writer.write_bytes(img.tobytes("raw", "BGRA"))
        bitmap = self._SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), self._BGRA8, img.width, img.height)
        result = self.loop.run_until_complete(self.engine.recognize_async(bitmap))
        return [join_cjk(line.text) for line in list(result.lines)]

    def close(self) -> None:
        self.loop.close()
