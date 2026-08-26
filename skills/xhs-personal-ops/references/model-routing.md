# Model routing

Use the locally authenticated Codex CLI for every agent:

```json
{
  "model_config": {
    "provider": "codex_cli",
    "model": "",
    "agent_reasoning": {
      "topic": "high",
      "golden": "high",
      "content": "high",
      "outline": "medium",
      "cover": "medium",
      "image": "low"
    }
  }
}
```

Use a compatible Base URL for Topic/Golden/Content and Codex CLI for visual roles:

```json
{
  "model_config": {
    "provider": "http",
    "providers": {
      "http": {"base_url": "https://provider.example/v1", "api_key": "environment only", "model": "text-model"},
      "codex_cli": {"model": "", "codex_cli_path": "codex"}
    },
    "agent_providers": {
      "topic": "http",
      "golden": "http",
      "content": "http",
      "outline": "codex_cli",
      "cover": "codex_cli",
      "image": "codex_cli"
    }
  }
}
```

Supply secrets through environment variables. Never save a real key in a config, run record, or Canva package.
