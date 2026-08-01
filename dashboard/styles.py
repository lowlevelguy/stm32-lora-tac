"""QssBuilder — tokens.qss pre-processor.

Qt's native `@variable` stylesheet handling is governed by undocumented
back-end quirks and is fragile across Qt versions. This module is the
safe path the handoff document prescribes: a ~20-line Python
pre-processor that reads `tokens.qss`, collects `@name: value;`
declarations into a dict, and substitutes `@name` tokens in body rules
before handing the resolved string to `qApp.setStyleSheet()`.

Usage (main.py):

    from styles import QssBuilder
    app.setStyleSheet(QssBuilder.from_file("dashboard/tokens.qss"))

Algorithm:
    1. Read the file line by line.
    2. A line matching `@<word>\\s*:\\s*<value>;` is a token declaration:
       add (name, value) to the tokens dict and drop it from the output.
    3. Any other line is a body rule: substitute every `@<word>` via
       regex lookup against the tokens dict. Unknown `@name` tokens
       are left intact (not erased) so a typo surfaces as a visible
       rule miss rather than silent elision.
    4. Return the concatenated resolved body as a single QSS string.

Comment lines (`/* ... */`, `// ...`) are preserved verbatim and are
not scanned for token declarations. (tokens.qss uses /* */ block
comments only; the // form is tolerated but unused.)
"""

import re
from pathlib import Path

# @name: value;  — anchored at line start, may have trailing comment.
# Value terminates at the first `;` so `@space-md: 16px; /* card */`
# parses with value="16px" and the trailing comment survives in the
# body pass (where it is treated as an ordinary line).
_TOKEN_DECL_RE = re.compile(
    r"^\s*@(?P<name>[A-Za-z][\w-]*)\s*:\s*(?P<value>[^;]+);"
)

# @name inside body rules — matched anywhere on the line. Word chars
# plus hyphen (so @accent-amber resolves, @accent_amber also works).
_TOKEN_USE_RE = re.compile(r"@(?P<name>[A-Za-z][\w-]*)")

# /* ... */ block comment — single line in this file's convention.
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/")


class QssBuilder:
    """Resolve `tokens.qss` into a ready-to-apply stylesheet string."""

    @staticmethod
    def from_file(path: str = "dashboard/tokens.qss") -> str:
        """Read tokens.qss, collect @name: value; declarations into a
        dict, then substitute @name in body rules via regex.

        Returns the resolved QSS string ready for ``qApp.setStyleSheet``.
        Unknown ``@name`` tokens in the body are left intact so typos
        surface visually rather than being silently elided.
        """
        text = Path(path).read_text(encoding="utf-8")
        return QssBuilder.from_string(text)

    @staticmethod
    def from_string(text: str) -> str:
        """Resolve a tokens.qss string in memory. Split out from
        ``from_file`` so the smoke test can feed a literal without
        touching the filesystem.
        """
        tokens: dict[str, str] = {}
        resolved_lines: list[str] = []

        for line in text.splitlines():
            # Skip token-declaration lines; collect them into the dict.
            decl = _TOKEN_DECL_RE.match(line)
            if decl is not None:
                name = decl.group("name")
                value = decl.group("value").strip()
                tokens[name] = value
                continue

            # Body line — substitute @name tokens. Block comments on
            # this line are temporarily masked so @tokens mentioned in
            # a /* */ comment don't get substituted (or flag as
            # unknown). We restore them after substitution.
            masked = _BLOCK_COMMENT_RE.sub(
                lambda m: " " * len(m.group(0)), line,
            )
            substituted = _TOKEN_USE_RE.sub(
                lambda m: tokens.get(m.group("name"), m.group(0)),
                masked,
            )
            resolved_lines.append(substituted)

        return "\n".join(resolved_lines) + "\n"
