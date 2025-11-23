# WSL testing and uv cache setup

Running the quality gates on WSL with the project on a Windows-mounted
filesystem (e.g., `/mnt/c/...`) can hit `Invalid cross-device link` errors when
`uv` tries to hardlink its cache. Set these environment variables to force copy
mode and keep temp files on the Linux side:

```bash
export UV_CACHE_DIR=/tmp/cmdmox-uv-cache
export UV_TOOL_DIR=/tmp/cmdmox-uv-tools
export UV_LINK_MODE=copy
export TMPDIR=/tmp
```

Then run the usual commands, for example:

```bash
set -o pipefail
make check-fmt
make lint
make typecheck
make test
```

This keeps `uv` caches on the same filesystem as the build artifacts and avoids
cross-device linking issues under WSL.
