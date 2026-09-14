"""
parkmind — this is the ONLY __init__.py in the whole src/ tree.

Every subfolder (models/, services/, agents/, graph/, tools/, config/) is a
Python namespace package (PEP 420) — no __init__.py needed in any of them.
Python 3.3+ finds them automatically as long as `src/` is on the path
(pytest.ini and agent.py both handle that — see the root README).

If you ever need real package-init code (re-exports, __all__, etc.) for a
specific subfolder, that's a deliberate choice to make at that time — don't
add empty __init__.py files "just in case".
"""
