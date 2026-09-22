"""Hermetic child server for lifecycle tests with pre-seeded plans (not live LLM tests)."""
import copy
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))
from api import server

# Do not call the production loader: no local overrides, encrypted store, environment
# provider keys, MCP subprocesses, or external notification endpoints are consulted.
template = yaml.safe_load((ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))
config = {k: template[k] for k in ("models", "approval", "retry", "authorization", "test_runner") if k in template}
config.update(api_keys={}, base_urls={}, mcp_servers={})
server._load_models_config = lambda *a, **kw: copy.deepcopy(config)
production_lifespan = server.lifespan

@asynccontextmanager
async def offline_lifespan(app):
    async with production_lifespan(app):
        assert not server.state.llm_client.providers
        assert not server.state.mcp_clients
        # These tests exercise persisted plans, not generation. Live tests retain
        # the actual Designer and verify the report independently.
        server.state.designer = None
        yield

server.app.router.lifespan_context = offline_lifespan
if __name__ == "__main__":
    uvicorn.run(server.app, host="127.0.0.1", port=int(sys.argv[1]))
