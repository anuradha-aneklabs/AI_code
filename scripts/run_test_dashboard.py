import os
import uvicorn
from harvey.dashboard import app, _check_loopback_bind

if __name__ == "__main__":
    if "HARVEY_DB_PATH" not in os.environ:
        os.environ["HARVEY_DB_PATH"] = "data/validation/harvey_maam_e2e.db"
    os.environ["HARVEY_ALLOW_REAL_DB_WRITE"] = "1"
    bind = _check_loopback_bind("127.0.0.1")
    uvicorn.run(app, host=bind, port=5566, log_level="info", proxy_headers=False, server_header=False, date_header=False)
