"""平台抽象：daemon / cli 只從這裡拿作業系統相關的功能（Windows：win.py + uia.py；macOS：mac.py）。"""
from __future__ import annotations

import sys

IS_MAC = sys.platform == "darwin"

if IS_MAC:
    from .mac import (  # noqa: F401
        IncognitoProbe, acquire_mutex, ensure_capture_permission, foreground, idle_seconds, permission_app_name,
        pid_alive, set_dpi_aware, window_rect,
    )
else:
    from .uia import IncognitoProbe  # noqa: F401
    from .win import acquire_mutex, foreground, idle_seconds, pid_alive, set_dpi_aware, window_rect  # noqa: F401

    def ensure_capture_permission(log=print) -> bool:  # Windows 不需要額外授權
        return True

    def permission_app_name() -> str:
        return ""
