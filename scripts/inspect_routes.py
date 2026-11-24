import re
from pathlib import Path

content = Path("harvey/dashboard.py").read_text(encoding="utf-8")
routes = re.findall(r"@app\.(?:get|post|put|delete)\(['\"]([^'\"]+)['\"]", content)
for r in routes:
    if any(k in r for k in ["criteria", "discover", "provider", "profile", "signal", "research", "estimate"]):
        print(r)
