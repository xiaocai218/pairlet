#!/usr/bin/env python3
import os
from pathlib import Path
import sys


script = Path.home() / ".local/opt/codex-pairlet/tools/current/update-codex-pairlet.py"
if not script.is_file():
    sys.exit("Codex/Pairlet maintenance tools are missing; reinstall the tools bundle")
os.execv(sys.executable, [sys.executable, str(script), *sys.argv[1:]])
