"""Allow running herdr-settle with ``python -m herdr_settle``.

herdr plugin actions invoke the package this way.
"""

from __future__ import annotations

from herdr_settle.cli import main

if __name__ == "__main__":
    main()
