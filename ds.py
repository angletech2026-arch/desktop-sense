"""ds：desktop-sense 指令入口（python ds.py <命令>）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dsense.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
