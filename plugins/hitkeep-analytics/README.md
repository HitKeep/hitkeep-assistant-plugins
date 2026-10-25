# HitKeep Analytics

Five first-party analytics skills for Claude, ChatGPT desktop, and Codex. The skills are copied unchanged from the canonical [HitKeep repository](https://github.com/HitKeep/hitkeep); [source.json](source.json) records the exact commit and file hashes.

See the [installation guide](https://github.com/HitKeep/hitkeep-assistant-plugins#readme) for supported repository marketplaces and client instructions. This package contains native Claude and portable OpenAI manifests. It is not an approved public directory listing.

```sh
claude plugin install hitkeep-analytics --marketplace HitKeep/hitkeep-assistant-plugins
```

Connect your own HitKeep instance separately using the [MCP guide](https://hitkeep.com/guides/integrations/mcp/). Installation does not grant site access or establish a connection. OAuth availability depends on the deployed server version and configuration.

Only read-only, aggregate analytics workflows are included. No credentials, executable hooks, contributor skills, raw hit export, administration, or write tools are bundled.

MIT licensed; see [LICENSE](LICENSE).
