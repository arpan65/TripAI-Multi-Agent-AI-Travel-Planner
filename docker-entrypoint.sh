#!/bin/sh
# Install chrome-for-testing into the volume if not already present.
# The volume at /ms-playwright persists across container restarts and image
# updates, so this only runs once after a fresh deploy or volume wipe.
if [ ! -d "/ms-playwright/chromium-1222" ]; then
    echo "Installing chrome-for-testing into volume..."
    npx @playwright/mcp@0.0.73 install-browser chrome-for-testing
    echo "Browser install complete."
fi

exec uv run uvicorn app.api:app --host 0.0.0.0 --port 8000 --log-level info
