# supt-ai

AI-powered support assistant.

## Project Structure

```
supt-ai/
├── .github/          # Workflows, issue templates
├── cdk/              # Infrastructure as Code (AWS CDK)
├── docker/           # Docker assets for Lambda
├── backend/          # Serverless functions, shared logic
├── frontend/         # Website, dashboard, admin UI
├── docs/             # Documentation, architecture diagrams
├── scripts/          # Utility scripts (deploy, test, etc.)
└── shared/           # Common types, utils
```

## Getting Started

```bash
npm install
```

## Development

TODO

## Deployment

```bash
cd cdk && npx cdk deploy
```

## Acknowledgements

This project uses [PR-Agent](https://github.com/Codium-ai/pr-agent) by CodiumAI, licensed under the [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0).

## License

This project is licensed under the [MIT License](LICENSE).

Third-party dependencies are distributed under their own licenses. See [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) for details.
