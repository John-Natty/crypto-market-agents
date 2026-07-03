# Architecture Technique

Ce document explique l'organisation interne de Crypto Market Agents pour un
developpeur, un reviewer ou un recruteur qui decouvre le depot.

Le projet est une application Python multi-agents orientee analyse du marche
crypto. Il ne fait pas de trading automatique, ne connecte aucun wallet et ne
demande jamais de cle privee crypto.

## Vue Globale

```text
CLI
 |
 v
CryptoMarketOrchestrator
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

Le coeur du systeme est l'orchestrateur. Il connecte la configuration, les
clients API, les agents d'analyse, la synthese finale, le rendu des rapports et
la notification WhatsApp optionnelle.

## Structure Des Dossiers

```text
src/crypto_market_agents/
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
tests/
scripts/
docs/
.github/
```

- `src/crypto_market_agents/agents/` contient les agents d'analyse : prix,
  volatilite, news, fondamentaux/on-chain et synthese finale.
- `src/crypto_market_agents/clients/` contient les clients API lecture seule
  pour CoinGecko, NewsAPI et DefiLlama.
- `src/crypto_market_agents/notifications/` contient le client WhatsApp et le
  notifier qui transforme un rapport final en message court.
- `src/crypto_market_agents/reporting/` contient le rendu Markdown et JSON.
- `src/crypto_market_agents/security.py` centralise les garde-fous de securite
  et la redaction des secrets.
- `src/crypto_market_agents/http_utils.py` fournit retry, backoff et cache court
  en memoire pour les clients HTTP en lecture seule.
- `src/crypto_market_agents/logging_utils.py` configure les logs avec redaction
  des secrets.
- `src/crypto_market_agents/mock_data.py` genere les faux rapports utilises par
  le mode demo `--mock`.
- `tests/` contient les tests unitaires et les tests d'integration mockes.
- `scripts/` contient des scripts de smoke test et de demonstration locale.
- `docs/` contient la documentation technique.
- `.github/` contient GitHub Actions, CodeQL, Dependabot et les templates GitHub.

## Flux Reel

Quand on lance :

```bash
crypto-market-agents report
```

le flow principal est le suivant :

1. La CLI parse les arguments : coins, devise, requete news, protocoles,
   dossier de sortie et option WhatsApp.
2. `load_config()` charge la configuration depuis `.env` et l'environnement.
3. Les garde-fous refusent toute configuration qui activerait trading,
   withdrawals ou order execution.
4. `CryptoMarketOrchestrator` cree les clients necessaires :
   `CoinGeckoClient`, `NewsClient`, `DefiLlamaClient` et `WhatsAppClient`.
5. L'orchestrateur instancie les agents specialises.
6. Chaque agent appelle son client API et produit un `AgentReport`.
7. Si un agent echoue, l'orchestrateur encapsule l'erreur dans un rapport
   `failed` pour continuer le flow quand c'est possible.
8. `FinalSynthesisAgent` combine les `AgentReport` en un `FinalReport`.
9. Le rapport final est rendu en Markdown et JSON.
10. Les fichiers sont sauvegardes dans `reports/` ou dans le dossier indique par
    `--output-dir`.
11. WhatsApp est declenche uniquement si la configuration l'active et si la CLI
    ne l'a pas desactive avec `--no-whatsapp`.

Ce flux peut appeler des APIs externes si les clients reels sont utilises et que
la configuration le permet.

## Flux Mock

Quand on lance :

```bash
crypto-market-agents report --mock
```

la CLI utilise un chemin de demonstration sans dependance externe :

1. Aucun fichier `.env` n'est necessaire.
2. Aucun client CoinGecko, NewsAPI, DefiLlama ou WhatsApp reel n'est instancie.
3. Aucune API externe n'est appelee.
4. Aucun message WhatsApp reel n'est envoye, meme si l'environnement local
   contient `WHATSAPP_ENABLED=true`.
5. `mock_data.py` cree de faux `AgentReport` coherents pour les agents prix,
   volatilite, news et on-chain.
6. `FinalSynthesisAgent` reste le vrai moteur de synthese.
7. Le rendu Markdown et JSON reste le vrai rendu applicatif.
8. Les rapports mockes sont sauvegardes dans `reports/` ou dans `--output-dir`.

Le mode mock permet de presenter le projet, tester la CLI et verifier le rendu
sans cle API, sans Internet et sans compte WhatsApp Business.

## Clients API

Les clients sont volontairement simples, testables et limites a leur domaine.

- `CoinGeckoClient` recupere les prix, donnees marche, volumes, market cap,
  variations et donnees utiles aux agents prix/risque.
- `NewsClient` recupere des articles depuis NewsAPI pour alimenter l'analyse
  news et sentiment.
- `DefiLlamaClient` recupere TVL, protocoles, chains, stablecoins et donnees DeFi
  publiques.
- `WhatsAppClient` envoie uniquement des notifications texte optionnelles via
  WhatsApp Cloud API.

CoinGecko, NewsAPI et DefiLlama sont des clients lecture seule. `WhatsAppClient`
est le seul client qui effectue un `POST`, uniquement pour envoyer un message
texte optionnel. Il ne gere pas de trading, wallet, paiement ou transfert.

## Agents

- `PriceMarketAgent` analyse prix, volume, market cap, rang, variation 1h/24h/7j
  et proximite high/low 24h.
- `VolatilityRiskAgent` analyse amplitude 24h, variations absolues, ratio volume
  sur market cap et signaux de risque.
- `NewsSentimentAgent` analyse les actualites avec des regles simples de
  mots-cles positifs, negatifs et de risque.
- `OnchainFundamentalAgent` analyse les donnees DefiLlama disponibles :
  protocoles, TVL, chains, stablecoins et fees/revenue.
- `FinalSynthesisAgent` compare les rapports, detecte des contradictions simples
  et produit un rapport final clair.

Chaque agent specialise reste responsable de son interpretation locale. La
synthese finale agrege ensuite ces interpretations dans un format commun.

## Schemas De Donnees

Les schemas communs se trouvent dans `src/crypto_market_agents/schemas.py`.

- `AgentReport` est le format retourne par chaque agent specialise.
- `Finding` represente une observation actionable au sens analyse, sans conseil
  financier direct.
- `Source` decrit l'origine d'une information ou d'un jeu de donnees.
- `FinalReport` est le rapport agrege produit par `FinalSynthesisAgent`.
- `RiskLevel` normalise les niveaux `low`, `medium`, `high` et `critical`.
- `AgentStatus` normalise les statuts `success`, `partial`, `failed` et
  `skipped`.

Ce format commun permet a l'orchestrateur et au renderer de travailler avec des
agents differents sans connaitre leurs details internes.

## Reporting

Le reporting produit deux formats complementaires :

- Markdown pour une lecture humaine simple ;
- JSON pour une exploitation automatique, des tests ou une integration future.

Les rapports sont sauvegardes dans `reports/` par defaut :

```text
reports/report_YYYY-MM-DD_HHMM.md
reports/report_YYYY-MM-DD_HHMM.json
```

En mode mock, les fichiers utilisent un prefixe `mock_report_`.

## Securite

Le projet applique une posture stricte :

- pas de trading automatique ;
- pas d'ordre d'achat ou de vente ;
- pas de short ;
- pas de wallet ;
- pas de cle privee ;
- pas de seed phrase ;
- pas de permission retrait ;
- pas de permission trading ;
- WhatsApp desactive par defaut ;
- secrets stockes dans `.env` uniquement ;
- `.env` jamais commite ;
- redaction centralisee dans `security.py` ;
- logs securises via `logging_utils.py` ;
- CodeQL et Dependabot actifs cote GitHub.

Les variables de securite par defaut gardent le projet en lecture seule :

```env
WHATSAPP_ENABLED=false
TRADING_ENABLED=false
WITHDRAWALS_ENABLED=false
ORDER_EXECUTION_ENABLED=false
EXCHANGE_MODE=disabled
```

## Robustesse HTTP

Les clients lecture seule utilisent `http_utils.py` pour limiter les erreurs
temporaires :

- retry limite sur timeout, erreur reseau, HTTP `429`, `500`, `502`, `503` et
  `504` ;
- pas de retry sur HTTP `400`, `401`, `403` ou `404` ;
- backoff exponentiel configurable ;
- cache memoire TTL pour les `GET` reussis ;
- logs de retry et cache avec URLs redigees.

WhatsApp ne fait pas de retry automatique agressif. Un message WhatsApp est un
`POST`, et le projet evite le risque de double notification si la premiere
requete a deja ete acceptee cote API.

## Tests Et CI

La qualite est verifiee par plusieurs niveaux :

- tests unitaires ;
- tests d'integration mockes ;
- `make ci-local` pour rejouer les controles principaux ;
- GitHub Actions sur `push` et `pull_request` ;
- matrice Python 3.11 / 3.12 ;
- Ruff pour le lint ;
- Ruff format check ;
- Coverage avec seuil progressif ;
- scripts mockes sans API externe ;
- Docker build ;
- CodeQL ;
- Dependabot.

Les tests ne doivent pas appeler CoinGecko, NewsAPI, DefiLlama ou WhatsApp reels.

## Limites Actuelles

- Analyse sentiment simple par mots-cles.
- Cache memoire seulement, non persistant.
- Pas de dashboard.
- Pas de scheduler.
- WhatsApp limite aux messages texte simples.
- Pas de templates WhatsApp.
- Pas de scan Docker avance.
- Donnees mock statiques.

## Ameliorations Futures

- Ajouter un dashboard.
- Ajouter un scheduler.
- Ajouter GDELT comme fallback news.
- Ameliorer l'analyse sentiment.
- Ajouter un cache persistant optionnel.
- Ajouter des metriques d'execution.
- Ajouter un scan Docker avance.
- Ajouter des rapports HTML.
- Ajouter des templates WhatsApp.
