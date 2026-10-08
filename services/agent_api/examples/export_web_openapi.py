"""Export public OpenAPI only: no lifespan, database/model IO or HTTP listener."""

import json
import sys

from ..app.core.config import Settings
from ..app.main import create_app


def main():
    app = create_app(settings=Settings(backend_mode="unavailable"))
    print(json.dumps(app.openapi(), ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
