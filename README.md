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

Start a new session, then invoke `$hitkeep-analytics` or another bundled skill. To pin the marketplace to a published release, add it with `--ref vX.Y.Z` (for example `--ref v2.15.0` for the existing historical release).

## ChatGPT

Where native archive upload is available:

1. Select a [published app release](https://github.com/HitKeep/hitkeep/releases) and download its `hitkeep-analytics-X.Y.Z.zip`. The corresponding [assistant release](https://github.com/HitKeep/hitkeep-assistant-plugins/releases) links the same verified ZIP. The existing [2.15.0 archive](https://github.com/HitKeep/hitkeep-assistant-plugins/releases/download/v2.15.0/hitkeep-analytics-2.15.0.zip) remains available in its original location.
2. Open **Settings → Plugins → Browse catalog → Add → Upload plugin archive** and select the ZIP, then select **Add plugin**.
3. Review **HitKeep Analytics**, the selected version, and its five skills, then select **Install plugin**.
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

The package version identifies the skill distribution and its matching immutable Git tag. OAuth availability depends on the deployed HitKeep version and configuration; publishing this package does not release or deploy the HitKeep application.

## Verify and package

```sh
python3 scripts/package.py
python3 -m unittest discover -s tests
claude plugin validate .
claude plugin validate plugins/hitkeep-analytics
python3 scripts/package.py --output dist/hitkeep-analytics-2.15.0.zip
```

The verifier checks both native catalogs, matching manifest identities and versions, the exact skill allowlist, and every canonical file hash. The ZIP contains one plugin directory with both native manifests and the same skills; it has deterministic file order, timestamps, and permissions.

For an update, check out an immutable reviewed app commit, then use the existing packager:

```sh
python3 scripts/package.py --sync-from ../hitkeep --source-sha FULL_APP_COMMIT --version X.Y.Z
python3 scripts/package.py --verify-source ../hitkeep --source-sha FULL_APP_COMMIT
```

Sync reads committed Git objects, stages and verifies all generated files before replacement, and records the app and packager commits in `source.json`. It copies exactly the five product skills' `SKILL.md`, `references/procedure.md`, and `agents/openai.yaml` files plus `LICENSE`; contributor `.agents/skills/` content never enters the package. Both native manifests and the Claude catalog advance together; the OpenAI catalog retains its native schema. CI independently retrieves the recorded app commit and compares bytes, so rewriting a local inventory cannot authorize changed skills. Keep application `skills/` canonical; analytics instruction changes belong there first. Ask AI continues to consume only the product procedure references.

## Release delivery and recovery

The application release builds the assistant ZIP once from an exact app commit using an immutable reviewed packager commit and records its digest in the attested release candidate. This repository receives exactly `release_tag`, `source_sha`, and `package_sha256` through `sync-hitkeep-release.yml` on main. GitHub authorizes the native dispatch; the receiver independently verifies the public stable app release, unchanged tag, candidate signature, exact producer run and attempt, mandatory checks, package digest, native manifests, and upstream skill bytes. Downloaded files are treated as data and never executed.

A read-only verification job must pass before the separate job receives its repository-scoped `GITHUB_TOKEN` write permission. The writer verifies again, rechecks current main and repository policy, and fast-forwards only the generated plugin, catalogs, and license. The workflow currently requires unprotected main and no repository or inherited rulesets; a policy change stops delivery for maintainer review. It never bypasses protection or force-pushes.

Each version has an immutable native installation ref `vX.Y.Z` and an assistant release linking the app ZIP. The assistant release uploads no second archive. Newer versions advance main and the native catalogs; backports receive only their historical tag and linked release, preserving newer catalogs and latest. Existing `v2.15.0` assets and refs remain unchanged.

To recover a partially completed delivery:

1. Open the failed native receiver run and rerun its failed jobs. The receiver revalidates inputs and current remote state, then resumes after a completed main update, tag, or release. Matching retries write nothing; conflicting immutable content stops for investigation. A canceled or failed caller watch does not cancel or prove failure of the receiver: inspect the exact receiver run first.
2. For a core app channel failure, use the app's native publication workflow with its original candidate run, attempt, and channel. Native reruns include dependent leaf jobs; an expired candidate requires a newly validated candidate. A successful assistant or other leaf need not be rolled back because another channel failed.
3. Resolve an explicit authentication or protection error before retrying. The receiver does not widen permissions, replace tags/assets, or downgrade a channel to make a retry pass. If main changed during a write race, the non-forced push fails; a native retry reads and validates the new head.

The run summary records the source/tag/digest, output ref and main readback, and whether delivery advanced main or created a historical ref. A native installation proves client packaging only; authenticated read-only aggregate MCP access still requires the separate connection described above. Private docs and cloud deployment follow their own authorized workflows.

MIT licensed; see [LICENSE](LICENSE).
