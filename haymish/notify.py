"""macOS user notifications via osascript.

No extra dependencies and no special entitlements needed for a CLI tool — a
"display notification" AppleScript one-liner is enough. The script text is an
AppleScript string literal, so title/message must be escaped for that context
(backslashes first, then double-quotes) rather than interpolated raw: a photo
filename or an LLM response could otherwise break out of the literal and
inject AppleScript. The escaped script is still passed to osascript via argv
(never through a shell), so there's no separate shell-injection layer to
worry about on top of that.
"""

from __future__ import annotations

import subprocess


def escape_applescript_string(text: str) -> str:
    """Escape text for embedding in a double-quoted AppleScript string literal.
    Shared by any module that builds an osascript command (see menubar.py) --
    unescaped interpolation lets the value break out of the literal and inject
    arbitrary AppleScript."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def notify(title: str, message: str) -> None:
    script = (
        f'display notification "{escape_applescript_string(message)}" '
        f'with title "{escape_applescript_string(title)}"'
    )
    subprocess.run(["osascript", "-e", script], capture_output=True, check=False)
