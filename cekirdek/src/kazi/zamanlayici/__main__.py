"""`python -m kazi.zamanlayici [--bir-kez] [--kuru]` CLI giriş noktası."""
from __future__ import annotations

import sys

from .calistir import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
