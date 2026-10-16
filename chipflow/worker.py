import sys
from pathlib import Path

from .engine import Worker

if __name__ == "__main__":
    Worker(Path(sys.argv[1])).run()
