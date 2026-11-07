# Scanner de marchés EUR Bitvavo

La commande `scan` découvre en lecture seule les marchés cotés en EUR et produit une présélection pour une analyse ultérieure. Elle ne crée ni portefeuille, ni ordre simulé ou réel, et n'appelle aucun LLM. Un marché découvert par cette commande n'est pas nécessairement récemment lancé.

## Données et sources

Trois requêtes publiques groupées, sans clé : `GET /v2/markets`, `GET /v2/ticker/24h` et `GET /v2/ticker/book`. La documentation Bitvavo indique pour le ticker 24 h les champs `open`, `last`, `volume` (actif de base), `volumeQuote` (actif de cotation) et `timestamp` en millisecondes Unix; la réponse sans filtre couvre tous les marchés (poids de limite 25, contre 1 avec `market`). Les marchés fournissent `status`, `base`, `quote`, `minOrderInBaseAsset`, `minOrderInQuoteAsset`. Le carnet groupé donne bid/ask et tailles au meilleur niveau; le classement ne traite pas ces tailles ni le volume échangé comme une profondeur garantie.

Les nombres sont convertis en `Decimal`. Le pourcentage 24 h est calculé comme `(last / open - 1) * 100`. Le spread relatif est `(ask - bid) / ((ask + bid) / 2) * 100`. `collected_at` est l'heure de réception UTC locale; `source_at` provient du `timestamp` du ticker. Aucun timestamp d'exchange n'est attribué au carnet.

## Filtres et classement

Par défaut, seuls les marchés `trading` sont éligibles, volume quote minimum 100 000 EUR et spread maximum 1 %. Paramètres CLI : `--min-volume-eur` et `--max-spread-pct`; `--include-unavailable` permet de conserver les statuts indisponibles dans la liste analysée, mais ils ne reçoivent pas de score. Les réponses manquantes, valeurs invalides/non finies, timestamp absent et bid supérieur à ask sont rejetés avec motifs.

Score déterministe, borné entre 40 et 100 : 40 points si les données passent les filtres; 30 points si le spread ne dépasse pas la moitié du seuil configuré; jusqu'à 30 points pour la variation positive observée, plafonnée à 30. Une baisse n'apporte pas de points. Le volume n'apporte pas de points supplémentaires au-delà du seuil. Le score ordonne une présélection de qualité/liquidité observée, pas une prédiction ou garantie de rendement. En égalité, volume quote décroissant puis symbole alphabétique.

Le seuil de volume est une activité passée, pas la profondeur actuellement disponible pour exécuter une taille donnée. Le meilleur bid/ask ne décrit que la première limite du carnet. Les données publiques sont instantanées, peuvent diverger entre appels ou être limitées; un HTTP 429 arrête proprement le scan et doit être suivi d'une attente. Pas de retry agressif.

## Usage

```sh
crypto-market-agents scan
crypto-market-agents scan --min-volume-eur 250000 --max-spread-pct 0.6 --json reports/market-scan.json
```

Le fichier JSON contient les candidats classés et les marchés EUR rejetés avec leurs motifs. Aucun service payant ou dépendance supplémentaire n'est utilisé.

Documentation officielle : [Get markets](https://docs.bitvavo.com/docs/rest-api/get-markets/), [Get ticker data 24h](https://docs.bitvavo.com/docs/rest-api/get-ticker-data-24-h/), [Get ticker book](https://docs.bitvavo.com/docs/rest-api/get-ticker-book/).

## Analyse historique approfondie

La commande `analyze` lit le JSON produit par `scan`, sélectionne les cinq premiers candidats par défaut et fait au plus une requête candles par actif. Elle utilise le endpoint public `GET /v2/{market}/candles?interval=...&limit=...`; la limite Bitvavo est de 1 à 1440 bougies, chaque ligne contient timestamp début d'intervalle en ms, open/high/low/close et volume en actif de base. Les réponses API sont les plus récentes d'abord; elles sont validées et normalisées en ordre UTC croissant. Les intervalles supportés par cette analyse sont `1h`, `4h` et `1d`. Une bougie n'est incluse que si timestamp + durée d'intervalle <= heure de collecte locale.

Formules avec les clôtures valides : variation d'horizon `(clôture courante / clôture n bougies avant - 1) * 100`; horizons 24 h, 7 j, 30 j (respectivement 1/7/30 bougies en 1d, 6/42/180 en 4h, 24/168/720 en 1h). Volatilité : écart-type échantillonnal (dénominateur n-1) des rendements simples consécutifs, exprimé en pourcentage par intervalle, sans annualisation. Tendance : SMA arithmétiques des 20 et 50 dernières clôtures; comparaison de la clôture courante à SMA20 et SMA20 à SMA50. Volume : dernière bougie divisée par la moyenne arithmétique des 20 bougies précédentes, volume en unité de base. Données absentes à l'horizon requis restent nulles/insuffisantes. Les observations contiennent les horodatages et valeurs des seules bougies clôturées.

Contrôles : format et OHLC positifs/cohérents, valeurs finies, doublons supprimés par timestamp, ordre corrigé et signalé, trous consignés sans interpolation. Une série fraîche doit avoir sa dernière bougie clôturée âgée d'au plus deux intervalles; l'analyse requiert au moins 30 clôtures et mentionne le besoin de 50 pour SMA50. Chaque erreur HTTP ou de données est contenue au candidat concerné. Les signaux favorables décrivent seulement le passé; aucune probabilité ni cible de prix n'est calculée. Une hausse ne garantit rien.

```sh
crypto-market-agents scan --json reports/market-scan.json
crypto-market-agents analyze --scan-json reports/market-scan.json
crypto-market-agents analyze --scan-json reports/market-scan.json --count 8 --interval 4h --limit 360 --json reports/analysis-4h.json
```

Documentation candles : [Get candlestick data](https://docs.bitvavo.com/docs/rest-api/get-candlestick-data/) (endpoint, intervalles, réponse, limite et poids 1). L'analyse peut être incomplète si l'historique disponible ne couvre pas chaque horizon; un trou n'est jamais comblé artificiellement.

## Comparaison d'évaluateurs

`compare` consomme le rapport `analyze` et crée `analysis-snapshot.json` à côté de ce rapport. Ce snapshot versionné copie les candidats, observations, mesures, qualités, sources/horodatages et limites. Son SHA-256 couvre son contenu JSON canonique, identifiant, version et date de création compris; les évaluateurs ne reçoivent que des copies de ce contenu. Le fichier est réutilisé pour les comparaisons suivantes et toute altération de contenu fait échouer la validation de l'empreinte.

L'évaluateur déterministe `deterministic-screen.v1` émet `watch` si l'historique est suffisant et frais, sans trou/données invalides, avec au moins deux horizons positifs et SMA20 supérieure à SMA50; `avoid` si les conditions de qualité passent, au moins deux horizons sont négatifs et SMA20 est sous SMA50; `abstain` autrement. Ces étiquettes servent uniquement à comparer le comportement, sans achat/vente/hold de portefeuille, probabilité ou rendement.

L'évaluateur Ollama réutilise le transport local, désactive proxy et redirections, n'expose aucun outil et reçoit les données comme observations non fiables. Sa réponse doit respecter un schéma fermé : une étiquette autorisée, un marché présent, et des références présentes dans le snapshot. Timeout, serveur absent, JSON invalide ou référence inconnue donnent abstention et erreur typée. Aucun téléchargement de modèle n'est lancé.

```sh
crypto-market-agents compare --analysis-json reports/market-analysis.json
crypto-market-agents compare --analysis-json reports/market-analysis.json --with-ollama --model llama3.2 --timeout 45 --json reports/comparison.json
```

Sans `--with-ollama`, aucun service Ollama n'est contacté. Le rapport documente durées, abstentions, erreurs, accords et divergences, sans désigner de gagnant : cette comparaison mesure seulement le comportement sur les mêmes données.
