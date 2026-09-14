"""Run the local Harbor Lantern backend."""

import uvicorn

from harbor_lantern.config import load_settings

if __name__ == "__main__":
    settings = load_settings()
    uvicorn.run("harbor_lantern.api.app:create_app", factory=True,
                host=settings.host, port=settings.port, proxy_headers=False)
