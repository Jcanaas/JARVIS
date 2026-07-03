import sys

# Source modules print emoji/unicode status lines (e.g. "[Planner] ✅ ...").
# On Windows the default console codec is cp1252, which can't encode them —
# reconfigure stdout/stderr so the test suite doesn't crash on those prints.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
