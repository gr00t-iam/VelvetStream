"""Launch VelvetStream: python velvetstream.py"""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from velvetstream.runtime import configure_runtime

    configure_runtime()
    if len(sys.argv) in (3, 5) and sys.argv[1] == "--diagnostics":
        from tools.diagnostics import run_diagnostics

        url = sys.argv[4] if len(sys.argv) == 5 and sys.argv[3] == "--youtube" else None
        raise SystemExit(run_diagnostics(sys.argv[2], url))
    from velvetstream.ui import main

    raise SystemExit(main())
