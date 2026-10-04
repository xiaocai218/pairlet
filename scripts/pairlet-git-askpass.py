#!/usr/bin/env python3
import os
from pathlib import Path
import sys

if "username" in sys.argv[1].lower():
    print("xiaocai218")
else:
    path = Path.home() / "github/release-token.txt"
    if path.stat().st_mode & 0o077:
        sys.exit("Credential file must be private")
    print(path.read_text().strip())
