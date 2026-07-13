$ErrorActionPreference = 'Stop'

if (-not $env:RUNTIME_AGENT_BEARER_TOKEN) {
    throw '请先设置 RUNTIME_AGENT_BEARER_TOKEN'
}

uv run python -m runtime_agent
