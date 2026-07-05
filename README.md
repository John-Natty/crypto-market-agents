# Crypto Market Agents

Projet Python multi-agents pour analyser le marche crypto a partir de donnees publiques, produire un rapport final clair, et envoyer en option un resume WhatsApp.

Le projet est strictement oriente analyse. Il ne fait pas de trading automatique, ne connecte aucun wallet, ne demande aucune cle privee crypto et n'execute jamais d'ordre d'achat ou de vente.

## Objectif Du Projet

Crypto Market Agents combine plusieurs sources de donnees pour produire une vue lisible du marche crypto :

- CoinGecko pour les prix, volumes, market cap et variations de marche ;
- NewsAPI pour les actualites crypto et une analyse de sentiment ponderee explicable ;
- DefiLlama pour les donnees DeFi et fondamentales publiques ;
- WhatsApp Cloud API en option pour envoyer un resume ou une alerte de risque.

Le resultat principal est un `FinalReport` exploitable en Markdown, JSON, HTML
et notification courte.

## Fonctionnalites Principales

- Chargement de configuration depuis `.env`.
- Garde-fous de securite contre le trading, les retraits et les cles privees.
- Clients API lecture seule pour CoinGecko, NewsAPI et DefiLlama.
- Agents specialises produisant des `AgentReport`.
- Agent de synthese finale produisant un `FinalReport`.
- Rendu du rapport final en Markdown, JSON et HTML autonome avec visualisations CSS simples.
- Notification WhatsApp optionnelle, desactivee par defaut.
- Orchestrateur global et premiere commande CLI.
- Mode mock officiel pour demo complete sans API externe.
- Scheduler local leger pour relancer les rapports a intervalle regulier.
- Dockerfile et Docker Compose pour execution conteneurisee.
- Qualite code avec Ruff et couverture de tests avec Coverage.
- Retry, backoff et cache court en memoire pour les clients API lecture seule.
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
Markdown / JSON / HTML / WhatsApp optionnel
```

## Documentation

- Architecture technique : [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Guide de contribution : [CONTRIBUTING.md](CONTRIBUTING.md)
- Politique de securite : [SECURITY.md](SECURITY.md)

## Orchestrateur Et CLI

`CryptoMarketOrchestrator` relie les briques existantes :

- charge la configuration ;
- cree les clients API lecture seule ;
- lance les agents specialises ;
- transmet les `AgentReport` a `FinalSynthesisAgent` ;
- sauvegarde le rapport final en Markdown, JSON et HTML ;
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
reports/report_YYYY-MM-DD_HHMM.html
```

Le fichier HTML est autonome : il contient son CSS integre et ne necessite pas
Internet, CDN ou JavaScript externe. Il inclut des visualisations simples :
cartes de synthese, barre de confidence globale, repartition des risques par
agent, confidence par agent et findings regroupes par niveau de risque.

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
reports/mock_report_YYYY-MM-DD_HHMM.html
```

Commande Makefile equivalente :

```bash
make cli-mock
```

En mode mock, aucun client CoinGecko, NewsAPI, DefiLlama ou WhatsApp n'est instancie par la CLI.

## Scheduler Local

La CLI fournit un scheduler local leger pour relancer le meme pipeline a
intervalle regulier, sans cron, sans daemon et sans dependance externe.

Lancer un seul rapport mock planifie :

```bash
crypto-market-agents schedule --mock --runs 1
```

Lancer le scheduler mock avec un intervalle d'une heure :

```bash
crypto-market-agents schedule --mock --interval-minutes 60
```

Lancer le scheduler en mode reel toutes les 6 heures, sans WhatsApp :

```bash
crypto-market-agents schedule --interval-minutes 360 --coins bitcoin ethereum --no-whatsapp
```

Commande Makefile equivalente en mode mock :

```bash
make scheduler-mock
```

Le scheduler affiche le mode utilise, l'intervalle, chaque run, les chemins des
rapports generes, le risque global, la confidence et le prochain run prevu. Sans
`--runs`, il continue jusqu'a interruption clavier avec `Ctrl+C`.

## Dashboard Local

Le dashboard local permet de consulter les rapports deja generes dans `reports/`
sans relancer les agents, sans base de donnees et sans appel API externe.

Generer un rapport de demonstration :

```bash
crypto-market-agents report --mock
```

Lancer le dashboard :

```bash
crypto-market-agents dashboard --reports-dir reports
```

Par defaut, il est disponible sur :

```text
http://127.0.0.1:8000
```

Options disponibles :

```bash
crypto-market-agents dashboard --reports-dir reports --host 127.0.0.1 --port 8000
```

Commandes Makefile :

```bash
make dashboard
make dashboard-mock
```

Le dashboard lit seulement les fichiers `.json`, `.html` et `.md` du dossier
configure. Il n'appelle pas CoinGecko, NewsAPI, DefiLlama ou WhatsApp, et il
n'envoie jamais de notification.

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

Analyse les actualites crypto depuis NewsAPI avec un scoring pondere explicable :

- categories de signaux : adoption, institutional, regulation, security, market_stress, legal, technical, macro, neutral ;
- mots-cles positifs et negatifs avec poids differencies ;
- intensite forte ou faible pour ajuster legerement les scores ;
- detection de risques sensibles : hack, exploit, security breach, liquidation, crackdown, insolvency, bankruptcy ;
- extraction d'assets mentionnes dans les articles avec regex a limites de mots ;
- sentiment global `positive`, `negative`, `mixed` ou `neutral`.

Cette analyse n'utilise pas d'IA externe, pas OpenAI, pas HuggingFace et pas de dependance lourde. Elle reste une aide pedagogique d'analyse, pas un conseil financier.

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

## Robustesse API

Les clients CoinGecko, NewsAPI et DefiLlama utilisent un helper HTTP commun pour les requetes `GET` lecture seule.

- retry limite uniquement sur erreurs temporaires : timeout, erreur reseau, HTTP `429`, `500`, `502`, `503`, `504` ;
- aucun retry automatique sur HTTP `400`, `401`, `403` ou `404` ;
- backoff exponentiel simple, par exemple `0.5s`, `1.0s`, `2.0s` avec les valeurs par defaut ;
- cache court pour les `GET` reussis, avec backend `memory` par defaut et backend `file` optionnel ;
- logs de cache hit/miss et retry avec URLs redigees, sans afficher de cle API ni token.

Variables disponibles :

```env
HTTP_MAX_RETRIES=2
HTTP_BACKOFF_SECONDS=0.5
HTTP_CACHE_TTL_SECONDS=60
HTTP_CACHE_ENABLED=true
HTTP_CACHE_BACKEND=memory
HTTP_CACHE_DIR=.cache/crypto-market-agents
```

Pour un scheduler local ou des demos repetees, le backend fichier peut etre active :

```env
HTTP_CACHE_ENABLED=true
HTTP_CACHE_BACKEND=file
HTTP_CACHE_DIR=.cache/crypto-market-agents
HTTP_CACHE_TTL_SECONDS=300
```

Les entrees du cache fichier sont des fichiers JSON autonomes. Le nom de fichier est un hash de la requete, donc les URLs, cles API et tokens ne sont pas visibles dans les noms de fichiers.

WhatsApp envoie des requetes `POST`. Le client WhatsApp ne fait pas de retry automatique afin d'eviter un risque de double notification si la premiere requete a deja ete acceptee cote API.

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

### Reseau Et Robustesse API

```env
REQUEST_TIMEOUT_SECONDS=20
HTTP_MAX_RETRIES=2
HTTP_BACKOFF_SECONDS=0.5
HTTP_CACHE_TTL_SECONDS=60
HTTP_CACHE_ENABLED=true
HTTP_CACHE_BACKEND=memory
HTTP_CACHE_DIR=.cache/crypto-market-agents
```

- `REQUEST_TIMEOUT_SECONDS` : timeout global par defaut.
- `HTTP_MAX_RETRIES` : nombre maximum de tentatives supplementaires sur erreurs temporaires.
- `HTTP_BACKOFF_SECONDS` : delai de base du backoff exponentiel.
- `HTTP_CACHE_TTL_SECONDS` : duree de vie du cache pour les requetes `GET` reussies.
- `HTTP_CACHE_ENABLED` : active ou desactive le cache court.
- `HTTP_CACHE_BACKEND` : `memory` par defaut, ou `file` pour conserver le cache sur disque.
- `HTTP_CACHE_DIR` : dossier du cache fichier, ignore par Git et Docker.

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
- `scripts/test_full_pipeline_mock.py` : simule le pipeline complet avec donnees mockees, genere Markdown, JSON et HTML.
- `scripts/test_orchestrator_mock.py` : lance `CryptoMarketOrchestrator` avec des agents factices.
- `scripts/test_cli_mock.py` : lance la CLI officielle en mode mock, sans API externe.
- `scripts/test_scheduler_mock.py` : lance le scheduler local en mode mock, sans API externe.
- `scripts/test_dashboard_mock.py` : genere un rapport mock et verifie le rendu dashboard sans serveur long-running.

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
make scheduler-mock
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
python3 scripts/test_scheduler_mock.py
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
HTTP_MAX_RETRIES=2
HTTP_BACKOFF_SECONDS=0.5
HTTP_CACHE_TTL_SECONDS=60
HTTP_CACHE_ENABLED=true
HTTP_CACHE_BACKEND=memory
HTTP_CACHE_DIR=.cache/crypto-market-agents
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

## Contribution Et Securite

Les contributions sont bienvenues si elles respectent la posture lecture seule
du projet.

- Guide de contribution : [CONTRIBUTING.md](CONTRIBUTING.md)
- Politique de securite : [SECURITY.md](SECURITY.md)

Avant de proposer une pull request, lance les controles locaux :

```bash
make ci-local
```

Ne commit jamais `.env`, de vraie cle API, de token, de cle privee crypto ou de
seed phrase. Les tests doivent rester mockes et ne doivent envoyer aucun message
WhatsApp reel.

## Logging Et Protection Des Secrets

La redaction des secrets est centralisee dans `crypto_market_agents.security`.

- les URLs sont parsees avec `urllib.parse` avant redaction ;
- les parametres sensibles comme `api_key`, `access_token`, `token`, `authorization`, `password` ou `secret` sont masques ;
- les credentials de type `user:password@host` sont masques ;
- les fragments d'URL ne sont pas conserves dans les messages rediges ;
- les mappings imbriques peuvent etre rediges avant affichage ;
- les logs configures via `crypto_market_agents.logging_utils` appliquent aussi la redaction.

Les cles API et tokens doivent rester dans `.env`. Les erreurs et logs ne doivent pas exposer de secret brut, et `.env` ne doit jamais etre commit.

## Structure Du Projet

```text
crypto-market-agents/
  README.md
  CONTRIBUTING.md
  Makefile
  SECURITY.md
  .env.example
  .github/dependabot.yml
  .github/pull_request_template.md
  .github/ISSUE_TEMPLATE/bug_report.md
  .github/ISSUE_TEMPLATE/feature_request.md
  .github/workflows/codeql.yml
  .github/workflows/tests.yml
  .dockerignore
  Dockerfile
  docker-compose.yml
  docs/
    ARCHITECTURE.md
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
      http_utils.py
      logging_utils.py
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

- Orchestrateur disponible en version simple, sans scheduler de production.
- Scheduler local disponible, sans daemon ni orchestration distribuee.
- CLI disponible en version initiale, sans sous-commandes avancees.
- CI GitHub Actions disponible avec matrice Python 3.11/3.12, lint et coverage.
- Securite GitHub legere avec Dependabot et CodeQL.
- Docker disponible en version simple, sans publication d'image ni registry.
- Coverage disponible avec seuil progressif de 80 %.
- Analyse sentiment ponderee par dictionnaires explicables, sans IA externe.
- Extraction assets/protocoles rule-based.
- Donnees live dependantes des APIs externes.
- WhatsApp limite aux messages texte simples.
- Pas encore de templates WhatsApp.
- Pas de webhook entrant.
- Dashboard local disponible, sans authentification ni usage production.
- Pas de planification automatique de rapports.

## Ameliorations Futures

- Enrichir l'orchestrateur global.
- Enrichir la CLI.
- Ajouter d'autres versions Python a la matrice si necessaire.
- Augmenter progressivement le seuil de couverture si la base de tests continue de se stabiliser.
- Ajouter un scan Docker avance dans une etape separee si necessaire.
- Enrichir le dashboard local si le besoin apparait.
- Enrichir progressivement les dictionnaires de sentiment et les jeux de tests.
- Ajouter GDELT comme fallback news.
- Ajouter des rapports planifies.
- Ajouter des retries et rate-limits avances.
- Ajouter une publication d'image Docker si necessaire.
- Ajouter des templates WhatsApp dans une etape separee.

## Disclaimer Financier

Ce projet fournit uniquement une analyse informative et pedagogique. Il ne constitue pas un conseil financier, un conseil en investissement, une recommandation d'achat, de vente ou de conservation d'un actif crypto. Les marches crypto sont volatils et risques.
