# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial project scaffold with AWS CDK infrastructure
- Docker image setup for PR-Agent + custom wrapper
- Discord dry-run mode for safe testing
- Basic GitHub webhook handler skeleton

### Changed
- Updated CDK dependencies to latest stable versions

### Infrastructure
- Lambda + API Gateway setup for webhook endpoint
- Secrets Manager integration for GitHub + Discord tokens

## [0.1.0] - 2026-06-21
### Added
- Initial repository structure
- Basic README