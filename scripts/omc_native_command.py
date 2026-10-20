"""Recognize literal single commands without executing or rewriting evidence.

Only one unquoted terminal stderr-to-stdout redirect is optional. This is a
bounded recognizer, not a shell parser, interpreter, or containment boundary.
"""
import shlex
import stat
from pathlib import Path


def validation_profile(command):
    """One fixed runner invocation; its effect still needs a verified receipt."""
    args = single_argv(command)
    if len(args) == 7 and args[:6] == ['python3','scripts/omc_validation_command.py','run','--target','.','--profile']:
        return args[6]
    return None


class NativeStreamAlreadyExists(FileExistsError):
    """Only a collision opening the selected native stream, not other files."""


def single_argv(command):
    if not isinstance(command, str) or any(c in command for c in ('\n', '\r', '\0', '$', '`')):
        return []
    text = command.strip(' \t')
    outside, quote, escaped = [], None, False
    for char in text:
        if escaped:
            outside.append(False)
            escaped = False
        elif char == '\\' and quote != "'":
            outside.append(False)
            escaped = True
        elif quote:
            outside.append(False)
            if char == quote:
                quote = None
        elif char in "\"'":
            outside.append(False)
            quote = char
        else:
            outside.append(True)
    if quote or escaped:
        return []
    suffix = '2>&1'
    start = len(text) - len(suffix)
    if (text.endswith(suffix) and start > 0 and text[start-1] in ' \t'
            and all(outside[start:])):
        text, outside = text[:start].rstrip(' \t'), outside[:start]
    if any(flag and char in ';&|<>()#*?[]{}~' for char, flag in zip(text, outside)):
        return []
    try:
        return shlex.split(text)
    except ValueError:
        return []


def read_command_reason(root: Path, command: str, *, historical: bool = False) -> str | None:
    """Recognize bounded inspection, without running a command or reading contents.

    None means a supported invocation, not attested execution, content accuracy,
    or shell containment. Historical calls require evidence of the path state at
    that time; this V0 has no such evidence format and must not inspect today's
    filesystem as a substitute. Root/cwd and tool identity are caller assumptions.
    """
    args = single_argv(command)
    if not args:
        return 'syntax_unsupported'
    if (args[0] not in {'cat', 'sha256sum'} or len(args) < 2
            or any(name.startswith('-') for name in args[1:])):
        return 'command_unsupported'
    if historical:
        return 'historical_path_unverified'
    try:
        base = root.resolve(strict=True)
        for name in args[1:]:
            path = Path(name)
            path = path if path.is_absolute() else base/path
            # Do not normalize away a symlink followed by '..'. Check each
            # original component before resolving and reject excursions outside.
            relative = path.relative_to(base)
            current = base
            for part in relative.parts:
                current = current/part
                if stat.S_ISLNK(current.lstat().st_mode):
                    return 'path_unverified'
                current.resolve(strict=True).relative_to(base)
            if not stat.S_ISREG(current.lstat().st_mode):
                return 'path_unverified'
    except (OSError, ValueError, RuntimeError):
        return 'path_unverified'
    return None
