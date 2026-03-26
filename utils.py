"""Common utilities for supermarket scraping."""

import logging
import re
import sys
from typing import Dict

# ---------------------------------------------------------------------------
# ANSI colour codes
# ---------------------------------------------------------------------------

_RESET = "\033[0m"
_BOLD = "\033[1m"

# Per-level colours
_LEVEL_COLOURS: Dict[int, str] = {
    logging.DEBUG: "\033[36m",  # cyan
    logging.INFO: "\033[32m",  # green
    logging.WARNING: "\033[33m",  # yellow
    logging.ERROR: "\033[31m",  # red
    logging.CRITICAL: "\033[35m",  # magenta
}

# Per-chain name colours (used in the logger-name prefix)
_CHAIN_COLOURS: Dict[str, str] = {
    "tivtaam": "\033[94m",  # bright blue
    "shufersal": "\033[96m",  # bright cyan
    "yochananof": "\033[93m",  # bright yellow
    "carrefour": "\033[95m",  # bright magenta
    "main": "\033[92m",  # bright green
}

# ---------------------------------------------------------------------------
# Hebrew / bidi helpers
# ---------------------------------------------------------------------------

# Regex that matches one or more consecutive Hebrew characters (Unicode block
# U+0590–U+05FF) plus common punctuation that appears in Hebrew text.
_HEBREW_RE = re.compile(r"[\u0590-\u05FF\uFB1D-\uFB4F]+")

# Unicode bidi control characters
_RLE = "\u202b"  # RIGHT-TO-LEFT EMBEDDING
_PDF = "\u202c"  # POP DIRECTIONAL FORMATTING


def _fix_bidi(text: str) -> str:
    """Improve Hebrew text rendering in a TTY.

    Strategy (no external dependencies):
      1. Try to use ``python-bidi`` (``bidi.algorithm.get_display``) for full
         Unicode Bidi Algorithm rendering if it is installed.
      2. Otherwise fall back to wrapping each Hebrew run with Unicode bidi
         control characters (RLE…PDF) so that terminals with bidi support
         render the segment right-to-left without reversing individual letters.

    When output is not a TTY the text is returned unchanged.
    """
    if not _is_tty():
        return text

    # Fast path: no Hebrew characters at all
    if not _HEBREW_RE.search(text):
        return text

    # Attempt full bidi reordering via python-bidi
    try:
        from bidi.algorithm import get_display  # type: ignore[import]

        return get_display(text)
    except ImportError:
        pass

    # Fallback: wrap each Hebrew run with RLE … PDF so the terminal's own
    # bidi engine can render it correctly (works in iTerm2, macOS Terminal,
    # gnome-terminal, etc.)
    def _wrap_hebrew(m: re.Match) -> str:
        return f"{_RLE}{m.group()}{_PDF}"

    return _HEBREW_RE.sub(_wrap_hebrew, text)


def _is_tty() -> bool:
    """Return True if stdout is a real terminal (not redirected)."""
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


class ColourFormatter(logging.Formatter):
    """Logging formatter that adds ANSI colour codes when writing to a TTY.

    Format:
        HH:MM:SS [LEVEL   ] chain_name: message
    """

    _DATE_FMT = "%H:%M:%S"
    _FMT = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"

    def __init__(self, use_colour: bool = True) -> None:
        super().__init__(fmt=self._FMT, datefmt=self._DATE_FMT)
        self._use_colour = use_colour

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        if not self._use_colour:
            return msg

        # Colour the level indicator
        level_colour = _LEVEL_COLOURS.get(record.levelno, "")
        coloured_level = f"{level_colour}{record.levelname:<8}{_RESET}"
        msg = msg.replace(record.levelname.ljust(8), coloured_level, 1)

        # Colour the logger name if it matches a known chain
        name = record.name.split(".")[0]  # take first component only
        chain_colour = _CHAIN_COLOURS.get(name, "")
        if chain_colour:
            msg = msg.replace(
                f"] {record.name}:", f"] {chain_colour}{_BOLD}{record.name}{_RESET}:", 1
            )

        # Apply bidi fix to Hebrew characters in the message body so that
        # RTL text renders correctly in TTY terminals.
        if _is_tty() and _HEBREW_RE.search(record.getMessage()):
            # Only process the message portion to avoid garbling ANSI codes
            # in the prefix.  We identify the message suffix by splitting on
            # the logger name + colon separator.
            separator = f"{record.name}: "
            if separator in msg:
                prefix, _, body = msg.partition(separator)
                msg = prefix + separator + _fix_bidi(body)

        return msg


def get_module_logger(module_name: str) -> logging.Logger:
    """Get a logger for a specific supermarket module.

    Args:
        module_name: Name of the module (e.g. 'shufersal', 'tivtaam', 'yochananof').

    Returns:
        Logger configured for the specific module.
    """
    return logging.getLogger(module_name)


def get_browser_headers(referer_host: str) -> Dict[str, str]:
    """Generate browser-like headers for making requests to supermarket APIs.

    Args:
        referer_host: The host URL to use as referer
                      (e.g. 'https://www.shufersal.co.il').

    Returns:
        Dictionary of headers mimicking a legitimate browser request.
    """
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
        "Referer": f"{referer_host}/",
    }


# ---------------------------------------------------------------------------
# Backward compatibility shim — previously modules called setup_logging()
# themselves.  The main orchestrator now configures logging; this no-op
# prevents ImportErrors in case any external code still calls this.
# ---------------------------------------------------------------------------


def setup_logging() -> None:  # noqa: D401
    """No-op shim kept for backward compatibility.

    Logging is now configured by main.py (or by the caller when used as a
    library).  Calling this function does nothing.
    """
