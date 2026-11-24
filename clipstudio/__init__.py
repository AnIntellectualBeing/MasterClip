"""Local GTA highlights and Shorts studio."""

import os
import subprocess

__version__ = "1.4.0"

if os.name == "nt":
    # The desktop app has no console, so every FFmpeg / yt-dlp / ffprobe child would pop up its own
    # black console window while clips are made. Start every child process windowless unless the
    # caller chose flags itself.
    _popen_init = subprocess.Popen.__init__

    def _windowless_init(self, *args, **kwargs):
        if not kwargs.get("creationflags") and len(args) < 13:  # creationflags is Popen's 13th parameter
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        _popen_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _windowless_init
