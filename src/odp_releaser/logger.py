import logging

from rich.console import Console
from rich.logging import RichHandler

FORMAT = "%(message)s"
logging.basicConfig(
    level="NOTSET",
    format=FORMAT,
    datefmt="[%X]",
    # Log to stderr so command output (e.g. make-payload's JSON) can be
    # captured cleanly from stdout.
    handlers=[RichHandler(rich_tracebacks=True, console=Console(stderr=True))],
)

logger = logging.getLogger("odp-releaser")
