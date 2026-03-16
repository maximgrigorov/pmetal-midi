"""Allow running as `python -m pmetal.mcp_server` or `python -m pmetal`."""

import sys

if len(sys.argv) > 1 and sys.argv[1] == "--cli":
    from .cli import cli
    cli()
else:
    from .mcp_server import main
    main()
