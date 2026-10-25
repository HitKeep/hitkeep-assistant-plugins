# HitKeep assistant plugins

First-party [HitKeep](https://github.com/HitKeep/hitkeep) analytics skills for Claude, ChatGPT, and Codex. One package contains the five canonical product skills, unchanged from the source commit recorded in [`source.json`](plugins/hitkeep-analytics/source.json):

- `hitkeep-analytics`
- `hitkeep-traffic-diagnosis`
- `hitkeep-ai-visibility-analyst`
- `hitkeep-ecommerce-analyst`
- `hitkeep-tracking-verifier`

The package supplies instructions. Connect your own HitKeep instance separately to use its read-only, aggregate MCP tools. Installation does not create a connection, grant site access, or enable OAuth on your server. No credentials, fixed Cloud region, executable hooks, or contributor skills are bundled.

## Claude Code

```sh
claude plugin marketplace add HitKeep/hitkeep-assistant-plugins
claude plugin install hitkeep-analytics@hitkeep
claude plugin details hitkeep-analytics
```

Start a new session after installing. The five skills appear under the `hitkeep-analytics` plugin namespace; invoke `/hitkeep-analytics:hitkeep-analytics` for the general analytics workflow.

In the Claude desktop app, open **Settings → Plugins → Add → Add Marketplace → Add from a repository**, enter `https://github.com/HitKeep/hitkeep-assistant-plugins`, sync it, and install **HitKeep Analytics** where custom marketplaces are supported. Repository installation is separate from Anthropic directory review.

## Codex

```sh
codex plugin marketplace add HitKeep/hitkeep-assistant-plugins
codex plugin add hitkeep-analytics@hitkeep
```

Start a new session, then invoke `$hitkeep-analytics` or another bundled skill. To pin the marketplace to this release, add it with `--ref v2.15.0`.

## ChatGPT

Where native archive upload is available:

1. Download [`hitkeep-analytics-2.15.0.zip`](https://github.com/HitKeep/hitkeep-assistant-plugins/releases/download/v2.15.0/hitkeep-analytics-2.15.0.zip) from the [release](https://github.com/HitKeep/hitkeep-assistant-plugins/releases/tag/v2.15.0).
2. Open **Settings → Plugins → Browse catalog → Add → Upload plugin archive** and select the ZIP, then select **Add plugin**.
3. Review **HitKeep Analytics**, version **2.15.0**, and its five skills, then select **Install plugin**.
4. Use **Try in chat** to start with the installed plugin.

This route was verified in signed-in ChatGPT web: the native plugin detail displayed version `2.15.0`, read capability, all five skills, and the installed **Manage** and **Try in chat** actions. It creates a private install, separate from public directory publication.

The OpenAI marketplace catalog also supports local repository discovery in ChatGPT desktop Work mode and Codex, where those features are available:

```sh
git clone --branch v2.15.0 https://github.com/HitKeep/hitkeep-assistant-plugins.git
```

Open the cloned repository as your project, restart ChatGPT desktop, then choose **HitKeep** in the Plugins Directory and install **HitKeep Analytics**. The catalog lives at `.agents/plugins/marketplace.json` and points to the same portable package used by Codex.

This repository is a first-party distribution source. It is **not a published or approved listing in the universal ChatGPT/Codex directory**. Installing skills does not establish an authenticated connection. See the [official OpenAI packaging documentation](https://developers.openai.com/plugins/build/plugins) for current client support.

## Connect HitKeep

Follow the [HitKeep MCP guide](https://hitkeep.com/guides/integrations/mcp/) for your instance's canonical endpoint, client support, and authentication. Use an account authorized for the sites you intend to analyze. Keep tokens out of prompts and repository files.

The MCP surface is read-only and aggregate-only. These skills cannot configure sites, change goals, export raw hits, manage tokens, or perform billing or administration. Skill installation never expands the authenticated user's authority.

Package version `2.15.0` identifies this skill distribution. OAuth availability depends on the deployed HitKeep version and configuration; publishing this package does not release or deploy the HitKeep application.

## Verify and package

```sh
python3 scripts/package.py
python3 -m unittest discover -s tests
claude plugin validate .
claude plugin validate plugins/hitkeep-analytics
python3 scripts/package.py --output dist/hitkeep-analytics-2.15.0.zip
```

The verifier checks both native catalogs, matching manifest identities and versions, the exact skill allowlist, and every canonical file hash. The ZIP contains one plugin directory with both native manifests and the same skills; it has deterministic file order, timestamps, and permissions.

For an update, export only the five skill directories and `LICENSE` from the next reviewed commit of `HitKeep/hitkeep`, update their hashes and commit in `source.json`, and update both manifest versions and the Claude catalog version together. Keep `skills/` in the application repository canonical; changes to analytics instructions belong there first. Do not publish test fixtures or add credentials to a package.

MIT licensed; see [LICENSE](LICENSE).
