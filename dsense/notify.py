"""桌面通知：Windows 右下角 toast（借 PowerShell 的 AppID，不需要額外套件）；macOS 用 osascript。"""
from __future__ import annotations

import base64
import subprocess
import sys

_PS = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$t = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__T__'))
$b = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__B__'))
$x = New-Object Windows.Data.Xml.Dom.XmlDocument
$x.LoadXml('<toast><visual><binding template="ToastGeneric"><text></text><text></text></binding></visual></toast>')
$n = $x.GetElementsByTagName('text')
$n.Item(0).AppendChild($x.CreateTextNode($t)) | Out-Null
$n.Item(1).AppendChild($x.CreateTextNode($b)) | Out-Null
$id = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show([Windows.UI.Notifications.ToastNotification]::new($x))
"""


def toast(title: str, body: str) -> None:
    if sys.platform == "darwin":
        from .mac import toast as mac_toast
        mac_toast(title, body)
        return
    enc = lambda s: base64.b64encode(s.encode("utf-8")).decode()  # noqa: E731
    script = _PS.replace("__T__", enc(title[:120])).replace("__B__", enc(body[:400]))
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
           "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode()]
    try:
        subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass
