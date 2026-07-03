# Contributing

Thanks for helping improve Crypto Market Agents. The project is intentionally
read-only: no trading, no wallets, no private keys, and no financial advice.

## Local Setup

Clone the repository:

```bash
git clone <repo-url>
cd crypto-market-agents
```

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the project and developer tools:

```bash
make install
```

## Main Developer Commands

Run the main test suite:

```bash
make test
```

Run Ruff:

```bash
make lint
make format-check
```

Format Python files:

```bash
make format
```

Run tests with coverage:

```bash
make coverage
```

Replay the main CI checks locally:

```bash
make ci-local
```

Run the official mock demo without external APIs:

```bash
make cli-mock
```

Run the local scheduler in mock mode:

```bash
make scheduler-mock
```

Build the Docker image:

```bash
make docker-build
```

## Pull Request Workflow

Create a branch:

```bash
git checkout -b my-change
```

Make a focused change, then run:

```bash
make ci-local
```

Open a pull request with:

- a short explanation of the change;
- tests or checks you ran;
- any documentation update needed;
- confirmation that no secrets were added.

## Safety Rules For Contributions

- Never commit `.env`.
- Never add a real API key or token.
- Never add a crypto private key, seed phrase, wallet file, or wallet
  connection flow.
- Never add automatic trading, buy orders, sell orders, shorting, withdrawals,
  or order execution.
- Never send real WhatsApp messages in tests.
- Never add tests that require CoinGecko, NewsAPI, DefiLlama, WhatsApp, or any
  external API to be reachable.
- Keep tests mock-based and deterministic.
- Keep user-facing reports educational and avoid direct financial advice.

## Documentation

Update `README.md` when a change affects:

- installation;
- configuration;
- commands;
- Docker;
- CI;
- security behavior;
- public CLI behavior.

Update `SECURITY.md` if the safety model or reporting process changes.
