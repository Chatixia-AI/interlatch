"""The package's name before the rename to Interlatch, kept only for what installs written then still run.

Hooks, MCP servers and background tasks set up before the rename may run `python -m chronicle …` (__main__.py), and
Windows tasks `pythonw -m chronicle.windows …` (windows.py). Both forward to the interlatch package, which holds the
implementation; nothing else belongs here.
"""

from interlatch import __version__ as __version__
