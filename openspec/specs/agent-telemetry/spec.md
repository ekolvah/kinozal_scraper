# agent-telemetry Specification

## Purpose
Attributes the Claude Code telemetry of sessions run in this repository to this repository, so
its token usage can be separated from other projects' in the shared Grafana Cloud stack.

## Requirements

### Requirement: Project attribution
The project-scope Claude Code settings SHALL set `OTEL_RESOURCE_ATTRIBUTES` to a comma-separated
list of `key=value` entries that contains `vcs.repository.name=ekolvah/kinozal_scraper` and
`vcs.repository.url.full=https://github.com/ekolvah/kinozal_scraper`.

#### Scenario: Settings carry the project pairs
- **WHEN** the `env.OTEL_RESOURCE_ATTRIBUTES` value of `.claude/settings.json` is split on `,`
- **THEN** every entry MUST contain exactly one `=`, and the resulting pairs MUST include
  `vcs.repository.name` = `ekolvah/kinozal_scraper` and `vcs.repository.url.full` =
  `https://github.com/ekolvah/kinozal_scraper`

#### Scenario: A session exports under the project label
- **WHEN** a Claude Code session started in this repository exports token usage
- **THEN** its `claude_code_token_usage_tokens_total` series MUST carry
  `vcs_repository_name="ekolvah/kinozal_scraper"` in Prometheus
