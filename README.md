# supt-ai

Automated PR reviewer powered by [PR-Agent](https://github.com/Codium-ai/pr-agent) and [xAI Grok](https://x.ai). Runs as a serverless pipeline on AWS — receives GitHub webhooks, reviews pull requests, and posts results to Discord and/or directly on the PR.

## How it Works

```
GitHub PR event
     │
     ▼
┌──────────────┐     ┌─────────────┐     ┌──────────────────┐
│ API Gateway  │────▶│ Intake      │────▶│ SQS Review Queue │
│ POST /webhook│     │ Lambda      │     │                  │
└──────────────┘     └─────────────┘     └────────┬─────────┘
                      (validates                   │
                       signature,                  ▼
                       filters events)   ┌──────────────────┐
                                         │ Reviewer Lambda  │
                                         │ (Docker)         │
                                         └────────┬─────────┘
                                                  │
                                         ┌────────┴─────────┐
                                         ▼                   ▼
                                    ┌──────────┐     ┌─────────────┐
                                    │ Discord  │     │ GitHub PR   │
                                    │ Webhook  │     │ Comment     │
                                    └──────────┘     └─────────────┘
```

1. A PR is opened/updated on GitHub
2. GitHub sends a webhook to the API Gateway endpoint
3. The **Intake Lambda** verifies the HMAC-SHA256 signature, filters to supported events, and enqueues the job
4. The **Reviewer Lambda** picks up the message, authenticates as a GitHub App, runs PR-Agent with Grok, and routes the parsed review to configured output destinations

## Project Structure

```
supt-ai/
├── cdk/                  # AWS CDK infrastructure
│   ├── bin/app.ts        # CDK app entry point
│   └── lib/supt-ai-stack.ts  # Stack definition
├── docker/               # Reviewer Lambda container
│   ├── Dockerfile
│   ├── wrapper.py        # Lambda handler (SQS + direct invoke)
│   ├── config.toml       # App configuration
│   ├── intake/           # Intake Lambda (plain Python)
│   │   └── index.py
│   └── lib/
│       ├── config.py     # Settings (Secrets Manager + env + TOML)
│       ├── github_app.py # GitHub App JWT auth
│       ├── reviewer.py   # PR-Agent CLI invocation
│       ├── parser.py     # Review YAML extraction
│       ├── router.py     # Output destination routing
│       └── outputs/      # Pluggable output adapters
│           ├── discord.py
│           └── console.py
├── .github/workflows/
│   ├── ci.yml            # Lint + test on PRs
│   └── deploy.yml        # CDK deploy on push to main
└── CONTRIBUTING.md
```

## Prerequisites

- **AWS account** with CDK bootstrapped (`npx cdk bootstrap`)
- **Node.js 20+** (for CDK)
- **GitHub App** — create one with permissions: Pull Requests (read/write), Contents (read)
- **xAI API key** — for Grok LLM access
- **Discord webhook URL** (optional) — for review notifications

## Getting Started

### 1. Install CDK dependencies

```bash
cd cdk
npm install
```

### 2. Configure secrets

After the first deploy, populate the Secrets Manager secret (`supt-ai/config`) with:

| Key | Description |
|-----|-------------|
| `GITHUB_APP_ID` | Your GitHub App's ID |
| `GITHUB_APP_PRIVATE_KEY` | PEM private key for the App |
| `GITHUB_APP_INSTALLATION_ID` | Installation ID for your org/repo |
| `WEBHOOK_SECRET` | Shared secret for webhook signature verification |
| `XAI_API_KEY` | xAI API key for Grok |
| `DISCORD_WEBHOOK_URL` | Discord webhook URL (optional) |

### 3. Deploy

```bash
cd cdk
npx cdk deploy
```

The deploy output includes the webhook URL. Add it to your GitHub App's webhook configuration with content type `application/json` and your `WEBHOOK_SECRET`.

### 4. Configure GitHub webhook

Point your GitHub App or repo webhook at the deployed URL:
- **Payload URL:** `<WebhookUrl output>/webhook`
- **Content type:** `application/json`
- **Secret:** same value as `WEBHOOK_SECRET` in Secrets Manager
- **Events:** Pull requests

## Configuration

Application behavior is controlled via `docker/config.toml`:

```toml
[output]
destinations = ["discord", "console"]  # Where reviews are sent

[discord]
embed_color = 5814783  # Embed accent color

[pr_agent]
git_provider = "github"
publish_output = true   # Post review as a PR comment
verbosity_level = 2     # Full output for parsing
```

Settings priority (highest wins): **environment variables > Secrets Manager > config.toml**

## Supported Events

The intake Lambda processes these `pull_request` actions:
- `opened` — new PR created
- `synchronize` — new commits pushed to PR branch
- `reopened` — closed PR reopened
- `ready_for_review` — draft PR marked ready

Draft PRs are skipped automatically.

## CI/CD

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| CI | Push/PR to `main` | Lint, test |
| Deploy | Push to `main` (cdk/ or docker/ paths) | CDK deploy via GitHub Actions OIDC |

Deployment uses keyless auth — the GitHub Actions workflow assumes an IAM role via OIDC, scoped to the `production` environment.

## Local Development

For local testing, copy the env example and fill in your credentials:

```bash
cp docker/.env.example docker/.env
# Edit docker/.env with your values
```

You can invoke the reviewer Lambda directly:

```bash
# From the docker/ directory
python -c "from wrapper import handler; handler({'pr_url': 'https://github.com/owner/repo/pull/1'}, None)"
```

## Acknowledgements

This project uses [PR-Agent](https://github.com/Codium-ai/pr-agent) by CodiumAI, licensed under the [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0).

## License

This project is licensed under the [MIT License](LICENSE).

Third-party dependencies are distributed under their own licenses. See [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) for details.
