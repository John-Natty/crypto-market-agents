# Crypto Market Agents

Projet Python multi-agents pour analyser le marche crypto a partir de donnees publiques, produire un rapport final clair, et envoyer en option un resume WhatsApp.

Le projet est strictement oriente analyse. Il ne fait pas de trading automatique, ne connecte aucun wallet, ne demande aucune cle privee crypto et n'execute jamais d'ordre d'achat ou de vente.

## Objectif Du Projet

Crypto Market Agents combine plusieurs sources de donnees pour produire une vue lisible du marche crypto :

- CoinGecko pour les prix, volumes, market cap et variations de marche ;
- NewsAPI pour les actualites crypto et une analyse de sentiment simple ;
- DefiLlama pour les donnees DeFi et fondamentales publiques ;
- WhatsApp Cloud API en option pour envoyer un resume ou une alerte de risque.

Le resultat principal est un `FinalReport` exploitable en Markdown, JSON et notification courte.

## Fonctionnalites Principales

- Chargement de configuration depuis `.env`.
- Garde-fous de securite contre le trading, les retraits et les cles privees.
- Clients API lecture seule pour CoinGecko, NewsAPI et DefiLlama.
- Agents specialises produisant des `AgentReport`.
- Agent de synthese finale produisant un `FinalReport`.
- Rendu du rapport final en Markdown et JSON.
- Notification WhatsApp optionnelle, desactivee par defaut.
- Orchestrateur global et premiere commande CLI.
- Mode mock officiel pour demo complete sans API externe.
- Dockerfile et Docker Compose pour execution conteneurisee.
- Qualite code avec Ruff et couverture de tests avec Coverage.
- Tests unitaires et integration mockee sans Internet.

## Architecture Globale

```text
CoinGecko / NewsAPI / DefiLlama
        |
        v
Clients API lecture seule
        |
        v
Agents specialises
        |
        v
FinalSynthesisAgent
        |
        v
Markdown / JSON / WhatsApp optionnel
```

## Orchestrateur Et CLI

`CryptoMarketOrchestrator` relie les briques existantes :

- charge la configuration ;
- cree les clients API lecture seule ;
- lance les agents specialises ;
- transmet les `AgentReport` a `FinalSynthesisAgent` ;
- sauvegarde le rapport final en Markdown et JSON ;
- declenche WhatsApp uniquement si l'option est activee.

Commande principale :

```bash
crypto-market-agents report
```

Commande equivalente sans installer le point d'entree :

```bash
python3 -m crypto_market_agents.cli report
```

Arguments disponibles :

```bash
crypto-market-agents report \
  --coins bitcoin ethereum solana \
  --currency usd \
  --news-query "crypto OR bitcoin OR ethereum" \
  --protocols uniswap aave lido \
  --output-dir reports \
  --no-whatsapp
```

- `--coins` : CoinGecko coin IDs a analyser.
- `--currency` : devise de reference, par defaut `usd`.
- `--news-query` : requete NewsAPI optionnelle.
- `--news-language` : langue NewsAPI, par defaut `en`.
- `--protocols` : slugs DefiLlama a analyser.
- `--output-dir` : dossier de sauvegarde, par defaut `reports`.
- `--no-whatsapp` : desactive WhatsApp pour cette execution.
- `--mock` : lance une demo complete avec donnees fictives, sans API externe.
- `--mock-risk-level` : scenario mock `low`, `medium`, `high` ou `critical`.
- `--env-file` : chemin optionnel vers un fichier `.env`.

Les rapports sont sauvegardes sous la forme :

```text
reports/report_YYYY-MM-DD_HHMM.md
reports/report_YYYY-MM-DD_HHMM.json
```

## Mode Mock / Demo Sans API

Le mode mock permet de generer un vrai `FinalReport` sans `.env`, sans cle API, sans Internet et sans WhatsApp reel.

Commande principale :

```bash
crypto-market-agents report --mock
```

Choisir un scenario de risque :

```bash
crypto-market-agents report --mock --mock-risk-level high
```

Les niveaux disponibles sont :

- `low` : scenario calme avec signaux stables ;
- `medium` : quelques signaux moderes, utilise par defaut ;
- `high` : volatilite et news negatives fictives ;
- `critical` : scenario fictif extreme, par exemple hack/exploit simule.

Les rapports mockes sont sauvegardes dans `reports/` par defaut :

```text
reports/mock_report_YYYY-MM-DD_HHMM.md
reports/mock_report_YYYY-MM-DD_HHMM.json
```

Commande Makefile equivalente :

```bash
make cli-mock
```

En mode mock, aucun client CoinGecko, NewsAPI, DefiLlama ou WhatsApp n'est instancie par la CLI.

## Agents Disponibles

### PriceMarketAgent

Analyse les donnees de marche CoinGecko :

- prix actuel ;
- volume ;
- market cap ;
- rang market cap ;
- variations 1h, 24h et 7j ;
- high/low 24h ;
- signaux simples de hausse, baisse, volume ou proximite high/low.

### VolatilityRiskAgent

Analyse la volatilite et les risques de marche :

- amplitude 24h ;
- variations absolues 1h, 24h et 7j ;
- ratio volume / market cap ;
- mouvements brutaux ;
- signaux de risque `low`, `medium`, `high` ou `critical`.

### NewsSentimentAgent

Analyse les actualites crypto depuis NewsAPI avec des regles simples de mots-cles :

- signaux positifs ;
- signaux negatifs ;
- risques de hack, exploit, liquidation ou regulation ;
- sentiment global simple.

Cette version n'utilise pas encore de modele IA pour le sentiment.

### OnchainFundamentalAgent

Analyse des donnees publiques DefiLlama :

- TVL actuelle ;
- evolution de TVL disponible ;
- protocoles DeFi ;
- chains supportees ;
- donnees stablecoins ;
- fees/revenue si disponibles.

### FinalSynthesisAgent

Combine les `AgentReport` des agents specialises pour produire un `FinalReport` :

- resume global du marche ;
- risque global ;
- confidence globale ;
- findings cles ;
- assets/protocoles a surveiller ;
- warnings ;
- contradictions simples ;
- conclusion informative.

### WhatsAppNotifier

Formate un `FinalReport` pour WhatsApp et envoie en option :

- un resume court du rapport final ;
- une alerte si `global_risk_level` vaut `high` ou `critical`.

WhatsApp reste desactive par defaut et le projet fonctionne sans compte WhatsApp Business/API.

## Sources De Donnees

### CoinGecko

Utilise pour les prix et donnees marche.

La cle API CoinGecko est optionnelle selon l'usage et les limites de l'API.

### NewsAPI

Utilise pour recuperer des articles recents.

Une cle API NewsAPI est necessaire pour les scripts live qui appellent NewsAPI.

### DefiLlama

Utilise pour les donnees DeFi/fondamentales publiques.

La Free API DefiLlama ne demande pas de cle.

### WhatsApp Cloud API

Utilisee uniquement si `WHATSAPP_ENABLED=true`.

Par defaut, aucune requete WhatsApp n'est envoyee.

## Installation

Prerequis :

- Python 3.11 ou plus recent ;
- Git ;
- un environnement virtuel Python recommande.

Cloner le depot :

```bash
git clone <url-du-repo>
cd crypto-market-agents
```

Creer et activer l'environnement virtuel :

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Installer le projet :

```bash
python -m pip install --upgrade pip
python -m pip install -e .
```

Le projet utilise actuellement `pyproject.toml` et n'a pas de `requirements.txt`.

## Configuration Du Fichier .env

Copier le fichier d'exemple :

```bash
cp .env.example .env
```

Puis completer uniquement les variables necessaires.

### Application

```env
APP_ENV=development
LOG_LEVEL=INFO
BASE_CURRENCY=usd
WATCHLIST=bitcoin,ethereum,solana
REPORT_LANGUAGE=fr
REPORT_OUTPUT_DIR=reports
```

### CoinGecko

```env
COINGECKO_BASE_URL=https://api.coingecko.com/api/v3
COINGECKO_API_KEY=
COINGECKO_TIMEOUT=20
```

- `COINGECKO_BASE_URL` : URL de base CoinGecko.
- `COINGECKO_API_KEY` : optionnelle pour le mode public ou demo selon usage.
- `COINGECKO_TIMEOUT` : timeout en secondes.

### NewsAPI

```env
NEWS_PROVIDER=newsapi
NEWS_API_KEY=
NEWS_BASE_URL=https://newsapi.org/v2
NEWS_TIMEOUT=20
NEWS_LANGUAGE=en
NEWS_DEFAULT_QUERY=crypto OR bitcoin OR ethereum OR blockchain
NEWS_MAX_ARTICLES=10
```

- `NEWS_API_KEY` : necessaire pour les scripts live NewsAPI.
- `NEWS_BASE_URL` : URL de base NewsAPI.
- `NEWS_TIMEOUT` : timeout en secondes.
- `NEWS_LANGUAGE` : langue des articles.
- `NEWS_DEFAULT_QUERY` : requete par defaut.
- `NEWS_MAX_ARTICLES` : nombre maximum d'articles lus.

### DefiLlama

```env
DEFILLAMA_BASE_URL=https://api.llama.fi
DEFILLAMA_TIMEOUT=20
```

DefiLlama Free API ne demande pas de cle API.

### WhatsApp

```env
WHATSAPP_ENABLED=false
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_TO_NUMBER=
WHATSAPP_GRAPH_API_VERSION=v23.0
WHATSAPP_TIMEOUT=20
```

- `WHATSAPP_ENABLED=false` : aucune requete WhatsApp n'est envoyee.
- `WHATSAPP_ACCESS_TOKEN` : token Meta WhatsApp Cloud API, seulement si WhatsApp est active.
- `WHATSAPP_PHONE_NUMBER_ID` : identifiant du numero WhatsApp Business.
- `WHATSAPP_TO_NUMBER` : destinataire.
- `WHATSAPP_GRAPH_API_VERSION` : version Graph API Meta.
- `WHATSAPP_TIMEOUT` : timeout en secondes.

Le projet fonctionne sans WhatsApp.

### Garde-Fous De Securite

```env
EXCHANGE_MODE=disabled
TRADING_ENABLED=false
WITHDRAWALS_ENABLED=false
ORDER_EXECUTION_ENABLED=false
```

Si une option active le trading, les retraits ou l'execution d'ordres, la configuration est rejetee.

## Utilisation Des Scripts

Les scripts sont des outils de demonstration et de verification locale.

### Scripts live possibles

Ces scripts peuvent appeler des APIs reelles si `.env` est configure :

- `scripts/test_coingecko_client.py` : teste `CoinGeckoClient`, `ping()`, prix simples et donnees marche.
- `scripts/test_price_market_agent.py` : lance `PriceMarketAgent` avec CoinGecko.
- `scripts/test_volatility_risk_agent.py` : lance `VolatilityRiskAgent` avec CoinGecko.
- `scripts/test_news_sentiment_agent.py` : lance `NewsSentimentAgent` avec NewsAPI. Demande `NEWS_API_KEY` pour un vrai appel.
- `scripts/test_onchain_fundamental_agent.py` : lance `OnchainFundamentalAgent` avec DefiLlama.
- `scripts/test_whatsapp_notification.py` : affiche le message WhatsApp prevu et envoie uniquement si `WHATSAPP_ENABLED=true`.

Exemple :

```bash
python3 scripts/test_coingecko_client.py
```

### Scripts sans API externe

Ces scripts utilisent des donnees factices :

- `scripts/test_final_synthesis_agent.py` : cree de faux `AgentReport` et genere une synthese finale.
- `scripts/test_full_pipeline_mock.py` : simule le pipeline complet avec donnees mockees, genere Markdown et JSON.
- `scripts/test_orchestrator_mock.py` : lance `CryptoMarketOrchestrator` avec des agents factices.
- `scripts/test_cli_mock.py` : lance la CLI officielle en mode mock, sans API externe.

Exemple :

```bash
python3 scripts/test_full_pipeline_mock.py
```

Autre smoke test sans API externe :

```bash
python3 scripts/test_orchestrator_mock.py
```

## Lancement Des Tests

Les tests unitaires et d'integration mockee sont surs :

- pas d'appel CoinGecko reel ;
- pas d'appel NewsAPI reel ;
- pas d'appel DefiLlama reel ;
- pas d'appel WhatsApp reel ;
- pas de cle API reelle ;
- pas d'Internet requis.

Commandes officielles :

```bash
python3 -m compileall src tests scripts
python3 -m unittest discover -s tests
python3 scripts/test_full_pipeline_mock.py
```

## Qualite Code Et Coverage

Installer les outils de developpement :

```bash
python -m pip install -e ".[dev]"
```

Verifier le lint Ruff :

```bash
python -m ruff check src tests scripts
```

Verifier le format sans modifier les fichiers :

```bash
python -m ruff format --check src tests scripts
```

Formater les fichiers Python si necessaire :

```bash
python -m ruff format src tests scripts
```

Mesurer la couverture des tests :

```bash
python -m coverage run -m unittest discover -s tests
python -m coverage report --fail-under=80
```

Coverage est configure avec un seuil progressif de 80 % pour eviter les regressions importantes sans bloquer inutilement le projet.

## Makefile Et Commandes Developpeur

Un `Makefile` fournit les commandes locales principales.

Afficher l'aide :

```bash
make help
```

Installer le projet et les outils de dev :

```bash
make install
```

Lancer les tests principaux :

```bash
make test
```

Verifier le lint et le format :

```bash
make lint
make format-check
```

Formater le code Python :

```bash
make format
```

Mesurer la couverture avec le seuil 80 % :

```bash
make coverage
```

Rejouer localement les controles principaux de la CI :

```bash
make ci-local
```

Construire l'image Docker :

```bash
make docker-build
```

Lancer les scripts mockes sans API externe :

```bash
make mock
make orchestrator-mock
make cli-mock
```

## Docker

Le projet fournit un `Dockerfile` base sur `python:3.11-slim`.

L'image :

- installe le projet depuis `pyproject.toml` ;
- ne copie pas de fichier `.env` reel ;
- utilise des variables de securite par defaut ;
- lance `crypto-market-agents --help` par defaut ;
- permet de lancer les tests, les scripts mockes ou la CLI.

Construire l'image :

```bash
docker build -t crypto-market-agents .
```

Lancer les tests dans Docker :

```bash
docker run --rm crypto-market-agents python3 -m unittest discover -s tests
```

Lancer le pipeline mocke, sans API externe :

```bash
docker run --rm crypto-market-agents python3 scripts/test_full_pipeline_mock.py
```

Lancer l'orchestrateur mocke, sans API externe :

```bash
docker run --rm crypto-market-agents python3 scripts/test_orchestrator_mock.py
```

Lancer la CLI avec sauvegarde des rapports dans le dossier local `reports/` :

```bash
docker run --rm -v "$(pwd)/reports:/app/reports" crypto-market-agents crypto-market-agents report --no-whatsapp
```

Attention : cette commande CLI utilise les vrais clients et peut appeler CoinGecko, NewsAPI ou DefiLlama selon la configuration. Les scripts mockes restent les commandes sures pour un test sans Internet.

Lancer la CLI avec un fichier `.env` local :

```bash
docker run --rm --env-file .env -v "$(pwd)/reports:/app/reports" crypto-market-agents crypto-market-agents report
```

Le fichier `.env` ne doit jamais etre commite.

### Docker Compose

Un `docker-compose.yml` minimal est fourni pour construire l'image localement, monter `reports/`, garder les variables de securite desactivees, et lancer par defaut le pipeline mocke :

```bash
docker compose up --build
```

Il est aussi possible de lancer la CLI via Compose en surchargeant la commande :

```bash
docker compose run --rm crypto-market-agents crypto-market-agents report --no-whatsapp
```

## Integration Continue

Le projet contient un workflow GitHub Actions dans `.github/workflows/tests.yml`.

La CI se lance automatiquement sur :

- `push` ;
- `pull_request`.

Le job `test` utilise Ubuntu avec une matrice Python 3.11 et Python 3.12.
Sur chaque version Python, il lance :

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python3 -m compileall src tests scripts
python -m ruff check src tests scripts
python -m ruff format --check src tests scripts
python -m unittest discover -s tests
python -m coverage run -m unittest discover -s tests
python -m coverage report --fail-under=80
python3 scripts/test_full_pipeline_mock.py
python3 scripts/test_orchestrator_mock.py
python3 scripts/test_cli_mock.py
```

Le job `docker` est separe pour eviter de construire l'image plusieurs fois. Il depend du job `test` et lance :

```bash
docker build -t crypto-market-agents .
```

La CI n'utilise aucun secret GitHub et force des variables sures :

```env
WHATSAPP_ENABLED=false
TRADING_ENABLED=false
WITHDRAWALS_ENABLED=false
ORDER_EXECUTION_ENABLED=false
EXCHANGE_MODE=disabled
NEWS_API_KEY=
COINGECKO_API_KEY=
CRYPTOPANIC_API_KEY=
OPENAI_API_KEY=
```

Les tests CI utilisent des mocks et ne doivent appeler aucune API externe reelle.

## Securite Automatisee

Le projet ajoute une securite GitHub legere :

- Dependabot surveille les dependances Python declarees dans `pyproject.toml` ;
- Dependabot surveille aussi les actions GitHub utilisees dans `.github/workflows/` ;
- CodeQL analyse le code Python sur `push`, `pull_request` et chaque semaine ;
- aucun secret GitHub n'est necessaire pour ces controles ;
- la CI n'utilise aucune vraie cle API ;
- WhatsApp, trading, withdrawals et order execution restent desactives par defaut.

Les secrets doivent rester dans un fichier `.env` local et ne jamais etre commites dans Git.

## Structure Du Projet

```text
crypto-market-agents/
  README.md
  Makefile
  .env.example
  .github/dependabot.yml
  .github/workflows/codeql.yml
  .github/workflows/tests.yml
  .dockerignore
  Dockerfile
  docker-compose.yml
  pyproject.toml
  reports/
  scripts/
  tests/
  src/
    crypto_market_agents/
      agents/
      clients/
      notifications/
      reporting/
      config.py
      cli.py
      mock_data.py
      orchestrator.py
      schemas.py
      security.py
```

## Securite

Le projet applique une posture lecture seule :

- aucun trading automatique ;
- aucun ordre d'achat ;
- aucun ordre de vente ;
- aucun short ;
- aucune connexion wallet ;
- aucune cle privee demandee ;
- aucune seed phrase demandee ;
- aucune permission retrait ;
- aucune permission trading ;
- aucune API exchange connectee a cette etape ;
- WhatsApp desactive par defaut ;
- tokens et cles API uniquement dans `.env` ;
- `.env` ne doit jamais etre versionne ;
- l'image Docker n'embarque pas `.env` ;
- tests sans appel reel aux APIs externes ;
- erreurs et resultats ne doivent pas exposer de token.

Les garde-fous refusent les variables sensibles de type private key, seed phrase, trading active ou retrait active.

## Limites Actuelles

- Orchestrateur disponible en version simple, sans scheduler ni retry avance.
- CLI disponible en version initiale, sans sous-commandes avancees.
- CI GitHub Actions disponible avec matrice Python 3.11/3.12, lint et coverage.
- Securite GitHub legere avec Dependabot et CodeQL.
- Docker disponible en version simple, sans publication d'image ni registry.
- Coverage disponible avec seuil progressif de 80 %.
- Analyse sentiment simple par mots-cles.
- Extraction assets/protocoles rule-based.
- Donnees live dependantes des APIs externes.
- WhatsApp limite aux messages texte simples.
- Pas encore de templates WhatsApp.
- Pas de webhook entrant.
- Pas de dashboard.
- Pas de planification automatique de rapports.

## Ameliorations Futures

- Enrichir l'orchestrateur global.
- Enrichir la CLI.
- Ajouter d'autres versions Python a la matrice si necessaire.
- Augmenter progressivement le seuil de couverture si la base de tests continue de se stabiliser.
- Ajouter un scan Docker avance dans une etape separee si necessaire.
- Ajouter un dashboard.
- Ajouter une meilleure analyse sentiment.
- Ajouter GDELT comme fallback news.
- Ajouter des rapports planifies.
- Ajouter des retries et rate-limits avances.
- Ajouter une publication d'image Docker si necessaire.
- Ajouter des templates WhatsApp dans une etape separee.

## Disclaimer Financier

Ce projet fournit uniquement une analyse informative et pedagogique. Il ne constitue pas un conseil financier, un conseil en investissement, une recommandation d'achat, de vente ou de conservation d'un actif crypto. Les marches crypto sont volatils et risques.
