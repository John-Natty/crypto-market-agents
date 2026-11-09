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

La commande `analyze` lit le JSON produit par `scan`, sélectionne les cinq premiers candidats par défaut et fait au plus une requête candles par actif. Elle utilise le endpoint public `GET /v2/{market}/candles?interval=...&limit=...`; la limite Bitvavo est de 1 à 1440 bougies et le défaut de l'analyse est 1440. Chaque ligne contient le timestamp du début d'intervalle en ms, open/high/low/close et volume en actif de base. Les réponses de l'API sont chronologiques de la plus récente à la plus ancienne; elles sont validées et normalisées en ordre UTC croissant. Les intervalles supportés par cette analyse sont `1h`, `4h` et `1d`. Une bougie n'est incluse que si timestamp + durée d'intervalle <= heure de collecte locale.

### Intervalles sans échange et trous

La [documentation officielle Bitvavo sur les bougies](https://docs.bitvavo.com/docs/rest-api/get-candlestick-data/) précise que seules les périodes où des transactions ont eu lieu produisent une bougie. Une période sans transaction apparaît comme un trou; l'API ne renvoie pas de bougie de volume nul pour la représenter. Un trou entre deux bougies reçues est donc compatible avec une absence d'échanges et ne prouve pas, à lui seul, une panne ou une perte de données.

À l'inverse, une erreur de transport, un timeout, une réponse invalide, un HTTP 429, un autre statut HTTP ou une limite de taille de réponse est enregistré comme erreur de récupération; ce n'est pas assimilé à une période sans échange. Le client actuel demande les dernières bougies avec `limit` seulement, sans `start`/`end` ni pagination. Bitvavo accepte au plus 1440 éléments et, si `start` ou `end` sont utilisés, exige des timestamps alignés sur les bornes de l'intervalle. La limite compte les bougies retournées, pas une durée garantie : un marché avec peu d'échanges peut donc couvrir une période plus longue que prévu, tandis qu'un marché actif peut ne pas couvrir 30 jours avec 720 éléments.

Les seules bougies ne permettent pas de distinguer définitivement « aucune transaction » d'une omission de fournisseur ou d'un défaut de collecte. Si cette distinction est importante, il faut croiser l'historique des transactions ou une source indépendante. L'analyse ne remplit jamais les trous et ne crée jamais de bougies synthétiques.

Les variations 24 h, 7 j et 30 j recherchent une clôture dont l'horodatage est exactement celui de la dernière clôture moins l'horizon demandé. Elles restent nulles si cet instant manque; l'analyse ne substitue pas une clôture distante de plusieurs intervalles et ne la présente pas comme un rendement horaire. Volatilité : écart-type échantillonnal (dénominateur n-1) des rendements simples entre deux bougies adjacentes exactement espacées de l'intervalle demandé, exprimé en pourcentage par intervalle, sans annualisation. Les paires séparées par un trou sont exclues. Tendance : SMA arithmétiques des 20 et 50 dernières clôtures uniquement si chacune des fenêtres est entièrement espacée par des intervalles contigus; une fenêtre traversant un trou reste indisponible. Le volume compare la dernière bougie à la moyenne des 20 précédentes uniquement si les 21 horodatages forment une suite contiguë. Les observations contiennent les horodatages et valeurs des seules bougies clôturées.

Une variation sur 30 jours demande deux clôtures séparées exactement de 30 jours : 31 clôtures en intervalle 1d, 181 en 4h, 721 en 1h. Par conséquent, une limite de 720 ne peut jamais fournir les deux extrémités requises pour 30 jours en 1h, même avec une série sans trou. Le défaut est 1440 (maximum Bitvavo), mais une série réellement irrégulière peut encore manquer l'horodatage cible et produire une mesure indisponible.

Contrôles : format et OHLC positifs/cohérents, valeurs finies, horodatages dupliqués retirés, ordre corrigé et signalé, trous consignés sans interpolation. Le rapport expose pour chaque métrique sa disponibilité, ses horodatages, sa fenêtre, son motif d'indisponibilité et sa fraîcheur. Les données invalides, les lacunes observées (cause inconnue) et l'historique insuffisant sont rapportés séparément. Une lacune ne rend pas indisponible une fenêtre récente qui ne la traverse pas. L'état global est descriptif (`complete`, `partial` ou `unavailable`), jamais un filtre qui efface des métriques valides. Les signaux favorables décrivent seulement le passé; aucune probabilité ni cible de prix n'est calculée. Une hausse ne garantit rien.

```sh
crypto-market-agents scan --json reports/market-scan.json
crypto-market-agents analyze --scan-json reports/market-scan.json
crypto-market-agents analyze --scan-json reports/market-scan.json --count 8 --interval 4h --limit 360 --json reports/analysis-4h.json
crypto-market-agents analyze --scan-json reports/market-scan.json --interval 1h --limit 1440
```

Documentation candles : [Get candlestick data](https://docs.bitvavo.com/docs/rest-api/get-candlestick-data/) (endpoint, intervalles, réponse, limite et poids 1). L'analyse peut être incomplète si l'historique disponible ne couvre pas chaque horizon; un trou n'est jamais comblé artificiellement.

## Comparaison d'évaluateurs

`compare` consomme le rapport `analyze` et crée `analysis-snapshot.json` à côté de ce rapport. Ce snapshot versionné copie les candidats, observations, mesures, qualités, sources/horodatages et limites. Son SHA-256 couvre son contenu JSON canonique, identifiant, version et date de création compris; les évaluateurs ne reçoivent que des copies de ce contenu. Le fichier est réutilisé pour les comparaisons suivantes et toute altération de contenu fait échouer la validation de l'empreinte.

L'analyse produit le contrat de fenêtres `metric-window-quality.v1`. Le snapshot versionné est `market-analysis-snapshot.v2`; les anciens snapshots sont refusés et ne sont pas remplacés automatiquement. L'évaluateur déterministe `deterministic-screen.v2` déclare ses dépendances : `watch` exige au moins deux variations disponibles et fraîches parmi 24 h/7 j/30 j, ainsi qu'une comparaison SMA20/SMA50 disponible et fraîche, toutes deux positives; `avoid` exige les mêmes métriques disponibles et fraîches, avec au moins deux variations négatives et SMA20 sous SMA50. Les sorties restent des étiquettes de surveillance historique : `watch` n'autorise jamais un achat. Une métrique requise manquante ou périmée conduit à `abstain`; une lacune hors des fenêtres utilisées n'est pas un motif d'invalidation.

L'évaluateur Ollama (`candidate-evaluation.v5`) effectue un appel indépendant par candidat, sans relance automatique. Tous les appels proviennent du même snapshot v2 vérifié, dont l'identifiant et l'empreinte restent inchangés : aucun sous-snapshot n'est enregistré et aucune donnée n'est actualisée. Chaque requête ne contient que les métriques et qualités du candidat concerné. Le contexte omet les bougies et les diagnostics dupliqués; pour les longues listes d'horodatages, il indique les extrémités, le nombre et le fait qu'elles sont résumées. Le snapshot conserve les listes complètes.

Le transport reste local, sans proxy, redirection ni outil. `think=false` est un paramètre explicite de `/api/chat`, conformément à la [documentation Ollama](https://github.com/ollama/ollama/blob/main/docs/api.md); aucune consigne textuelle ne le remplace. Le [schéma de sortie structuré](https://ollama.com/blog/structured-outputs) limite chaque candidat à classification, motif d'abstention, arguments structurés et synthèse courte. Le validateur indépendant vérifie de nouveau chaque argument contre le snapshot. Les clés JSON dupliquées et toute génération arrêtée par `done_reason=length` sont rejetées, même si le contenu semble parseable. Aucun JSON tronqué n'est réparé.

Le rapport v5 sépare les observations, les arguments, la synthèse libre explicitement non certifiée, les limites et la classification. Un argument contient `metric_id`, relation constatée, référence exacte et rôle (favorable, défavorable ou limite). Pour une relation numérique, le validateur vérifie le signe ou la comparaison à 1 sur la valeur disponible du snapshot; pour les données non disponibles, il vérifie l'état exact. Le rendu ajoute lui-même valeur, unité, horizon, fenêtre, disponibilité, fraîcheur et motif depuis le snapshot. Le rôle reste une appréciation du modèle, pas une propriété mesurable. La synthèse libre ne constitue pas une preuve et ne change aucun contrôle.

Les états distinguent disponible, indisponible, périmé, invalide et non rapporté. Les lacunes sont exposées avec leur cause inconnue et les références des métriques indisponibles dont le motif signale une fenêtre interrompue; une lacune ne bloque pas les métriques récentes valides hors de sa fenêtre. Le volume absolu reste dans l'actif de base et le volume relatif est un multiple de la moyenne des vingt bougies antérieures. SMA20 supérieure à SMA50 exprime une comparaison de niveaux, pas une pente temporelle.

Le rapport distingue `abstention_reason=required_data_unavailable` (au moins une dépendance requise manque, est invalide ou périmée) de `abstention_reason=judgment` (données requises disponibles, le modèle choisit de ne pas conclure). Le validateur refuse une insuffisance inventée dans le second cas. Pour `watch` et `avoid`, le motif est `not_applicable`. Les exigences d'admissibilité restent séparées du sens de la stratégie : le modèle peut diverger de la classification déterministe si les preuves minimales sont disponibles.

**Admissibilité et classification sont distinctes.** `watch` et `avoid` demandent tous deux au moins deux variations numériques finies, disponibles et fraîches parmi 24 h/7 j/30 j, plus une comparaison SMA20/SMA50 numérique finie, disponible et fraîche, et une référence à `metrics`. Si ces preuves manquent, le schéma autorise uniquement `abstain`, et le validateur rejette indépendamment toute autre étiquette. Si elles sont présentes, Ollama peut choisir `watch`, `avoid` ou `abstain`, sans être obligé de reproduire le sens des variations exigé par la stratégie déterministe. Cette séparation corrige le validateur v2 qui imposait aussi le signal de la stratégie. Elle ne réduit aucun contrôle de disponibilité, de fenêtre, de fraîcheur ou de références. Une divergence est conservée. `watch` reste une surveillance historique sans autorisation d'achat.

La fraîcheur correspond à l'état constaté lors de l'analyse source : rejouer un snapshot ne transforme pas ses observations anciennes en données actuelles. La validation structurelle vérifie les références et l'admissibilité; elle ne prouve pas que chaque phrase du modèle est exacte ou que sa conclusion est pertinente. Les contenus reçus restent des données non fiables.

Les paramètres sont configurables et enregistrés : génération par appel `--num-predict` (défaut 1200, borne 128–8192), génération totale `--total-num-predict` (6000, borne 128–32768), contexte `--num-ctx` (8192, borne 4096–32768), durée par appel `--timeout` (défaut CLI 120 s, maximum 180 s), durée totale `--total-timeout` (180 s, maximum 600 s). Chaque réponse est limitée à 200 000 octets. La lecture vérifie une échéance absolue; le temps restant limite chaque nouvel appel. Le budget total réserve le plafond de chaque appel, y compris après une erreur de transport sans compte de tokens fiable. Un budget restant inférieur à 128 tokens ou une durée épuisée empêche les appels suivants (`not_requested`, `total_budget_exhausted`). Aucune boucle de relance n'existe.

Le rapport garde les sorties validées même si un autre candidat échoue : statut global `success`, `partial` ou `error`, avec statut, erreur, durée et métadonnées par candidat. Seuls les candidats validés des deux côtés contribuent aux accords et divergences. Les erreurs et appels non exécutés restent `non_comparable` avec leur motif; leurs abstentions de secours ne sont jamais des décisions du modèle. Aucun téléchargement de modèle n'est lancé.

Le diagnostic optionnel `--diagnostic-dir reports/ollama-diagnostics-local/...` conserve uniquement `message.content` dans des fichiers locaux uniques, créés avec permissions `0600`, sous `reports/` ignoré par Git. Il n'enregistre jamais le champ `thinking`, n'affiche pas la sortie brute dans les logs et n'alimente aucune décision. Les métadonnées conservent seulement le nombre de caractères du raisonnement séparé, les tailles, tokens, durées et motif de fin. Ces fichiers servent exclusivement à l'inspection manuelle et restent hors des commits.

Le diagnostic local des quatre erreurs `invalid_interpretation` du contrat v4 a reproduit les mêmes causes : l'expression régulière de rejet des chiffres correspondait au dernier chiffre des identifiants `SMA20`/`SMA50`, et aux horizons `24h`, `7d`, `30 jours`. ICNT contenait `SMA20`; HMSTR, `SMA20`, `SMA50` et `30 jours`; RSR, `SMA20`, `SMA50`, `24h` et `7 jours`; O, `24h`, `7d` et `30d`. Le rejet n'était donc pas un défaut de références. Le contrat v5 retire ce filtre sur le texte libre et ne lui donne aucun rôle probatoire. Il valide à la place les arguments structurés. Pour PEPE, le résumé v4 n'a pas déclenché ce rejet, mais affirmait que les lacunes rendaient toute évaluation fiable impossible alors que les mesures requises étaient disponibles; la nouvelle validation n'essaie pas de certifier ce texte libre.

Les rapports v4 précédents restent conservés et ne sont pas réinterprétés rétroactivement. Les captures de diagnostic contenant le texte brut restent locales, ignorées par Git et hors des décisions.

```sh
crypto-market-agents compare --analysis-json reports/market-analysis.json
crypto-market-agents compare --analysis-json reports/market-analysis.json --with-ollama --model qwen3:8b --timeout 60 --total-timeout 180 --num-predict 1200 --total-num-predict 6000 --num-ctx 8192 --json reports/comparison-v3.json
# Diagnostic local optionnel : ajouter --diagnostic-dir reports/ollama-diagnostics-local/run-1
```

Sans `--with-ollama`, aucun service Ollama n'est contacté. Le rapport documente durées, abstentions, erreurs, accords et divergences, sans désigner de gagnant : cette comparaison mesure seulement le comportement sur les mêmes données.


La comparaison réelle du contrat v3.4 (9 novembre 2026) a validé les cinq candidats en 8492 ms, avec 430 tokens générés au total. Ces rapports historiques sont conservés.

Les essais intermédiaires v5–v8 sont conservés. Le diagnostic v5 a montré que le modèle associait parfois le rôle défavorable à une mesure indisponible; ces arguments ont été rejetés. Il attribuait aussi `required_data_unavailable` à ICNT et PEPE alors que les dépendances étaient présentes, puis `not_applicable` à leurs abstentions. v6 a montré que la liste globale de relations laissait le modèle choisir `equal_to_one` pour des valeurs différentes de 1. v8 a montré un faux positif de synthèse sur l'identifiant `SMA20_vs_SMA50_pct`; le contrôle numérique l'interprétait comme une valeur libre. Ces cas ont conduit aux options d'argument listées par métrique, au calcul du motif d'abstention par le code, et à un contrôle numérique qui masque uniquement les noms d'indicateur et d'horizon connus. Chaque essai est conservé dans son propre rapport, sans remplacer les précédents.

La comparaison réelle finale v9 sur le même snapshot a validé les cinq candidats en 17 824 ms, avec 1 710 tokens générés. Les cinq classifications Ollama sont `abstain`: HMSTR, O et RSR ont `required_data_unavailable`, calculé par le code; ICNT et PEPE ont `judgment`, car leurs dépendances sont disponibles. Il y a 4 accords, 1 divergence (ICNT : stratégie déterministe `watch`, Ollama `abstain`) et aucune erreur/non-comparabilité. L'audit indépendant confirme les 24 arguments, leurs références et les valeurs, unités, fenêtres, états et fraîcheurs rendus depuis le snapshot. Les synthèses restent du texte libre non certifié. Le rapport et son audit sont dans `reports/metric-window-v2-release/`; le snapshot d'origine n'a pas changé. Aucun résultat ne mesure la performance financière ni la justesse future, et aucune donnée source n'est actualisée par une comparaison.

## Recherche autonome

La commande `research` fait choisir au modèle les candidats et outils à approfondir, dans des limites explicites et en lecture seule. Voir [la documentation de recherche autonome](autonomous-research.md) pour les outils admis, budgets, traçabilité et limites.
