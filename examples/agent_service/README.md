# Agent Service

Agent service is a FastAPI-based, multi-tenant and multi-session service built with AgentScope 2.0.

This example demonstrates

- how to set up the agent service with PostgreSQL storage and pgvector RAG,
- how to use Redis as the cross-process message bus, and
- how to launch the service and its companion Web UI

Details about the agent service please refer to the [tutorial](https://docs.agentscope.io/latest/en/deploy/agent-service).

## Prerequisites

- Python ≥ 3.11
- Node.js ≥ 20 with `npx`
- PostgreSQL with the `pgvector` extension available
- Redis for the message bus
- [optional] Gaode/AMap API key in `AMAP_API_KEY` (for the `amap` MCP)
- [optional] `DASHSCOPE_API_KEY` or `MEM0_API_KEY` for mem0 long-term memory

## Quickstart

Install AgentScope from PyPI or source:

```bash
uv pip install agentscope[full]
# or
# uv pip install -e [full]
```

Install PostgreSQL with pgvector and start Redis as the message bus:

```bash
# PostgreSQL + pgvector
docker run --rm -p 5432:5432 \
  -e POSTGRES_DB=agentscope \
  -e POSTGRES_USER=agentscope \
  -e POSTGRES_PASSWORD=agentscope \
  pgvector/pgvector:pg16

# Redis message bus
docker run --rm -p 6379:6379 redis:7
```

Configure the service if you are not using the local defaults:

```bash
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5432
export POSTGRES_DB=agentscope
export POSTGRES_USER=agentscope
export POSTGRES_PASSWORD=agentscope

export REDIS_HOST=localhost
export REDIS_PORT=6379
```

Long-term memory file backends use user-agent scope by default, so the same
user chatting with the same agent can reuse Agentic/ReMe file memory across
sessions:

```bash
export AGENT_SERVICE_MEMORY_SCOPE=user_agent  # default
# export AGENT_SERVICE_MEMORY_SCOPE=session   # opt into isolated sessions
```

Start the agent service:

```bash
cd examples/agent_service

python main.py
```

Launch the Web UI in a separate terminal to experience a chat-style interface:

```bash
cd examples/web_ui/

pnpm install
# or npm install

# Run in dev mode
pnpm dev
```

After that, you can set the API endpoint `http://localhost:8000` in the Web UI and start experiencing the agent service.

<img src="https://gw.alicdn.com/imgextra/i2/O1CN01Phmg1G1brIVC8WXyU_!!6000000003518-2-tps-2938-1736.png" alt="Web UI Screenshot" width="100%">

## What Next

- You can customize the service in `main.py` by adding your own MCPs, middlewares, or workspace manager implementations.

- Experience the agent service, including
    - human-in-the-loop interactions & permission system
<img src="https://gw.alicdn.com/imgextra/i1/O1CN01vGGiBw20agWwpzmjy_!!6000000006866-2-tps-2934-1732.png" alt="Permission System" width="100%">

    - schedule tasks
<img src="https://gw.alicdn.com/imgextra/i1/O1CN01Xi3Qw71E2haKKu4z0_!!6000000000294-2-tps-2932-1738.png" alt="Schedule Tasks" width="100%">

    - and more! (stay tuned for future updates)
