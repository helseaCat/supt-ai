# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- PR-Agent integration with Grok 4.3 (xAI) for AI code reviews
- Discord webhook output — review results posted as embeds
- Modular output router pattern (console, discord — pluggable destinations)
- Structured config loading from config.toml + environment overrides
- Architecture documentation
- docker/.env.example with all required environment variables
- API Gateway HTTP API with POST /webhook endpoint
- GitHub webhook signature verification (HMAC-SHA256)
- Dual invocation mode — direct (dry-run) and API Gateway (webhook)
- CDK stack with Lambda + API Gateway (Docker image built from source)

## [0.1.0] - 2026-06-21
### Added
- Initial repository structure
- Basic README