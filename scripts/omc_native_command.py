"""Recognize literal single commands without executing or rewriting evidence.

Only one unquoted terminal stderr-to-stdout redirect is optional. This is a
bounded recognizer, not a shell parser, interpreter, or containment boundary.
"""
import shlex


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
