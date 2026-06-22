# supt-ai Architecture

## What This System Does

A self-hosted AI code reviewer that:

1. Listens for GitHub PR events (open, update, comment commands)
2. Runs PR-Agent to analyze the diff using an LLM
3. Routes the output to one or more destinations (GitHub comments, Discord, console)

**Discord is the primary notification channel.** All non-GitHub output goes to Discord — dry-run results, status updates, error alerts. The output router is designed to support additional destinations later without code changes.

---

## System Layers

```
┌─────────────────────────────────────────────────┐
│  TRIGGERS                                       │
│  (what kicks off a review)                      │
├─────────────────────────────────────────────────┤
│  • GitHub Webhook (PR opened/updated/comment)   │
│  • CLI invocation (dry-run)                     │
└────────────────────────┬────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  HANDLER                                        │
│  (validates input, extracts PR context)         │
├─────────────────────────────────────────────────┤
│  • Verify webhook signature                     │
│  • Parse event type + PR URL                    │
│  • Determine which command to run               │
│    (/review, /improve, /describe, etc.)         │
└────────────────────────┬────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  REVIEW ENGINE                                  │
│  (the actual AI analysis)                       │
├─────────────────────────────────────────────────┤
│  • PR-Agent core (runs the command)             │
│  • Calls LLM via Bedrock (Claude / Grok)        │
│  • Returns structured review output             │
└────────────────────────┬────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  OUTPUT ROUTER                                  │
│  (where results go)                             │
├─────────────────────────────────────────────────┤
│  • GitHub (post as PR comment) ← production      │
│  • Discord (post via webhook)  ← primary notif   │
│  • Console (stdout)            ← local dev      │
│  • (Additional destinations pluggable later)     │
└─────────────────────────────────────────────────┘
```

---

## Key Design Decisions

### 1. Single Lambda, Multiple Triggers

One Lambda container handles both webhook events and (optionally) direct invocations.
The code path is the same — only the trigger and output destination differ.

**Why:** Simpler to maintain. One Docker image, one deployment, one set of dependencies.
You avoid syncing logic across multiple services.

### 2. Output Router as a Strategy

The output destination is a config choice, not a code fork. The review engine produces
a structured result, and the router sends it wherever config says.

```
config.toml:
  [output]
  destinations = ["github"]            # production: post to PR
  destinations = ["discord"]           # dry-run: send to Discord
  destinations = ["github", "discord"] # both: review + notify
```

**Why:** Destinations are pluggable. Discord is primary today; adding
others later is a config change + one new adapter, no engine refactoring.

### 3. PR-Agent as a Library, Not a Fork

Use PR-Agent's Python package and configure it, rather than forking the repo.
The `wrapper.py` is a thin adapter that:
- Sets up config (TOML → PR-Agent settings)
- Calls PR-Agent's CLI/API programmatically
- Captures output and passes it to the router

**Why:** Easier upgrades. PR-Agent releases frequently. A wrapper lets you pull new
versions without merge conflicts.

### 4. Secrets in Secrets Manager, Referenced by Name

- `supt-ai/github-token` — GitHub App private key or PAT
- `supt-ai/github-webhook-secret` — webhook signature verification
- `supt-ai/discord-webhook-url` — Discord channel webhook for review output

Lambda reads these at cold start and caches for the container lifetime.

**Why:** No secrets in env vars, no secrets in config files, easy rotation.
Adding a new destination means adding one more secret — no code deploy required.

---

## Data Flow: Normal PR Review

```
GitHub PR opened
      │
      ▼
API Gateway (POST /webhook)
      │
      ▼
Lambda: verify signature → parse event → extract PR URL
      │
      ▼
PR-Agent: fetch diff → build prompt → call Bedrock
      │
      ▼
Bedrock returns analysis
      │
      ▼
Output Router: post comment to GitHub PR
```

## Data Flow: Dry-Run (Discord Output)

```
Developer runs: ./scripts/dry-run.sh --pr-url <url>
      │
      ▼
Lambda invoke (direct) OR local Python execution
      │
      ▼
PR-Agent: fetch diff → build prompt → call Bedrock
      │
      ▼
Bedrock returns analysis
      │
      ▼
Output Router: POST to Discord webhook + print to console
      │
      ▼
(GitHub is NOT touched)
```

Discord receives a formatted embed with: PR title, summary, key findings, and a link
back to the PR. This is the default feedback loop for testing and iteration.

---

## AWS Resources (CDK will provision)

| Resource | Purpose |
|----------|---------|
| Lambda (Docker) | Runs PR-Agent + wrapper |
| API Gateway (HTTP API) | Receives GitHub webhooks |
| Secrets Manager (x3) | GitHub token, webhook secret, Discord webhook URL |
| IAM Role | Lambda execution: Bedrock invoke, Secrets read |
| CloudWatch Logs | Lambda output, errors |
| ECR Repository | Stores the Docker image |

---

## What Lives Where (directory mapping)

| Directory | Contains |
|-----------|----------|
| `cdk/` | All AWS infrastructure (API GW, Lambda, Secrets, IAM) |
| `docker/` | Dockerfile, wrapper.py, config.toml, requirements.txt |
| `scripts/` | Helper scripts (dry-run, deploy, test-webhook) |
| `docs/` | This file, deployment guide, ADRs |

---

## MVP Scope (build in this order)

1. **Docker image** — PR-Agent + wrapper that can run a review and print to console
2. **CDK stack** — Lambda + ECR + IAM (no API Gateway yet, just invoke directly)
3. **Output router** — Discord webhook integration (primary notification channel)
4. **API Gateway** — Wire up GitHub webhook trigger
5. **GitHub App** — Configure the app, webhook secret, permissions
6. **Dry-run script** — CLI that invokes Lambda or runs locally

---

## Open Questions

- [ ] VPC? Probably not needed for MVP (Lambda in public subnet is fine for Bedrock + GitHub API)
- [ ] Concurrency limits? Start with Lambda default, revisit if costs spike
- [ ] PR-Agent version pinning strategy? Pin in requirements.txt, update monthly?
- [ ] Multiple repos? Single GitHub App install covers all repos in the org
