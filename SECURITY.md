# Security Policy

Crypto Market Agents is a read-only market analysis project. It must never
receive crypto private keys, seed phrases, wallet credentials, withdrawal
permissions, or trading permissions.

## Supported Versions

The project is currently in MVP stage. Only the current default branch is
maintained for security fixes.

| Version | Supported |
| --- | --- |
| current default branch | Yes |
| older branches or forks | No |

## Reporting A Vulnerability

If you find a security issue, please report it privately to the project
maintainer.

Do not open a public GitHub issue if the report contains:

- an API key, token, webhook secret, or credential;
- a crypto private key, seed phrase, wallet file, or exchange credential;
- an exploitable vulnerability with step-by-step abuse details;
- logs that expose secrets.

When reporting a vulnerability, include:

- a short description of the issue;
- the affected file or command if known;
- safe reproduction steps using fake data only;
- redacted logs only;
- your suggested impact level if you have one.

## Secret Handling Rules

- Keep secrets in a local `.env` file only.
- Never commit `.env`.
- Never paste real API keys, WhatsApp tokens, exchange tokens, wallet secrets,
  private keys, or seed phrases into issues, pull requests, logs, tests, or docs.
- Use fake example values in tests and documentation.
- Logs and error messages are designed to redact sensitive values before display.

## Project Safety Boundaries

This project does not do automatic trading and must not execute buy, sell,
short, transfer, withdrawal, or order placement actions.

The default safety posture is:

- `WHATSAPP_ENABLED=false`;
- `TRADING_ENABLED=false`;
- `WITHDRAWALS_ENABLED=false`;
- `ORDER_EXECUTION_ENABLED=false`;
- `EXCHANGE_MODE=disabled`.

WhatsApp notifications are optional and disabled by default. Tests must never
send real WhatsApp messages.

## Scope

Security reports are welcome for:

- leaked or insufficiently redacted secrets;
- unsafe configuration defaults;
- accidental external API calls in tests;
- dependency or GitHub Actions issues;
- path, file, logging, or report rendering issues that could expose secrets.

Requests to add trading, wallet management, private key handling, or financial
advice are out of scope for this project.
