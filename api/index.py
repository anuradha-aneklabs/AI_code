"""
Vercel serverless function entrypoint for Harvey AI Sales Agent dashboard.
"""

import os
import sys
from pathlib import Path

# Add project root to sys.path so harvey package imports resolve
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from harvey.dashboard import app

# Export app as handler for ASGI serverless execution
handler = app
