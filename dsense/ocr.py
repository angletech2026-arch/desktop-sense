"""本機 OCR，完全不送雲端：Windows 用內建的 Windows.Media.Ocr，macOS 用 Apple Vision。

每個 Ocr 物件只能在建立它的那條執行緒使用（Windows 版綁一個 asyncio loop）。
"""
from __future__ import annotations

import asyncio
import io
import re
import sys

from PIL import Image

from .i18n import L

_CJK = "⺀-鿿豈-﫿　-〿＀-￯"
_CJK_GAP = re.compile(f"(?<=[{_CJK}])[ \t]+(?=[{_CJK}])")


def join_cjk(text: str) -> str:
    """Windows OCR 會在每個中文字之間塞空白：「建 置 失 敗」→「建置失敗」。"""
    return _CJK_GAP.sub("", text)


def vision_languages(language: str) -> list[str]:
    """設定的 OCR 語言（Windows 寫法，例：zh-Hant-TW）→ Apple Vision 的語言清單；空字串 = 自動偵測。"""
    lang = (language or "").strip()
    if not lang:
        return []
    low = lang.lower()
    if low.startswith("zh"):
        main = "zh-Hans" if ("hans" in low or low in ("zh-cn", "zh-sg")) else "zh-Hant"
    elif low.startswith("ja"):
        main = "ja-JP"
    elif low.startswith("ko"):
        main = "ko-KR"
    elif low.startswith("en"):
        return ["en-US"]
    else:
        main = lang
    return [main, "en-US"]  # 程式碼與錯誤訊息大多是英文：一律一起認


class OcrMac:
    """Apple Vision（VNRecognizeTextRequest），macOS 內建、離線。"""

    def __init__(self, language: str = "") -> None:
        import Vision  # pyobjc-framework-Vision
        from Foundation import NSData
        self._Vision, self._NSData = Vision, NSData
        self.langs = vision_languages(language)

    def recognize(self, img: Image.Image) -> list[str]:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "PNG")
        raw = buf.getvalue()
        V = self._Vision
        handler = V.VNImageRequestHandler.alloc().initWithData_options_(self._NSData.dataWithBytes_length_(raw, len(raw)), None)
        req = V.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(V.VNRequestTextRecognitionLevelAccurate)
        req.setUsesLanguageCorrection_(True)
        if self.langs:
            req.setRecognitionLanguages_(self.langs)
        elif hasattr(req, "setAutomaticallyDetectsLanguage_"):
            req.setAutomaticallyDetectsLanguage_(True)
        ok, err = handler.performRequests_error_([req], None)
        if not ok:
            raise OSError(f"Vision OCR failed: {err}")
        obs = list(req.results() or [])
        # Vision 的座標原點在左下：由上到下、由左到右排回閱讀順序
        obs.sort(key=lambda o: (-round(o.boundingBox().origin.y, 2), o.boundingBox().origin.x))
        lines = []
        for o in obs:
            cand = o.topCandidates_(1)
            if cand:
                lines.append(str(cand[0].string()))
        return lines

    def close(self) -> None:
        pass


class OcrWin:
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


Ocr = OcrMac if sys.platform == "darwin" else OcrWin
