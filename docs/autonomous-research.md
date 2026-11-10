# Recherche autonome en lecture seule

La commande `research` lance une boucle locale bornée à partir du catalogue
public des marchés Bitvavo en EUR. Elle ne lance pas le scanner et ne reçoit
aucune liste de favoris. Ollama choisit les pages et marchés à approfondir,
puis les outils historiques, fondamentaux et d'actualité utiles à l'objectif.

```bash
crypto-market-agents research \
  --objective "Comparer les candidats selon les preuves disponibles" \
  --model qwen3:8b \
  --json reports/autonomous-research-YYYYMMDD-HHMMSS.json
```

Le chemin JSON doit être nouveau. La commande refuse d'écraser un rapport.
Elle utilise le fichier de correspondances explicites
`data/asset-identity-mappings.json` et les clients déjà configurés. L'accès à
Ollama est local (`127.0.0.1:11434`); CoinGecko utilise le chemin gratuit
existant, avec son cache et ses délais. Aucune clé payante, aucun abonnement
et aucun service d'ordre ne sont nécessaires.

Le catalogue comprend uniquement les marchés cotés en EUR annoncés par
l'endpoint public `markets` de Bitvavo. Les observations regroupées viennent
des endpoints publics `ticker/24h` et `ticker/book`. Chaque marché indique le
statut, les prix, volumes, spread et minimums d'ordre disponibles, avec unités,
date locale de collecte, date source quand Bitvavo la fournit, et motif précis
pour les champs indisponibles. Les cours de carnet n'ont pas d'horodatage
source dans cet endpoint; l'heure de collecte n'est pas présentée comme heure
de transaction.

Les marchés EUR sont paginés par identifiant de marché croissant, avec dix
marchés par page. Cet ordre est une clé stable de pagination, pas un classement.
Aucun score, seuil de volume ou spread, statut de négociation ou performance
passée ne filtre les marchés présentés. Le modèle voit la première page, puis
peut demander d'autres pages avec `list_market_page`; chaque demande doit
conserver un motif bref. Le rapport donne le nombre total de marchés, les pages
consultées et celles non consultées. Une recherche terminée sur quelques pages
ne signifie pas que tout le catalogue a été étudié. L'univers ne comprend pas
automatiquement les actifs sans marché EUR Bitvavo, les autres plateformes ni
les marchés décentralisés.

La commande `scan` reste disponible séparément pour une présélection
déterministe facultative; `research` et `automatic-paper-cycle` n'utilisent
pas son classement.

## Outils que le modèle peut demander

- `list_market_page`: consulter une page neutre du catalogue EUR et choisir
  quelles pistes explorer ensuite.
- `analyze_candidate`: demander l'analyse historique d'un marché déjà
  consulté dans une page.
- `get_fundamentals`: dossier factuel d'identité, CoinGecko et éventuelle
  correspondance DefiLlama vérifiée.
- `get_market_news`: flux RSS/Atom généraux configurés, associés aux références
  d'un marché consulté pour le bilan.
- `get_asset_news`: actualités attribuables par le code à une identité d'actif
  vérifiée. Une identité non résolue ne permet pas d'attribution par symbole.
- `finish_research`: choisit les références de faits et d'informations
  manquantes à transmettre à la phase de conclusion sans outil.

Les arguments sont validés par le programme. Les mappings explicites vérifiés
restent prioritaires et ne sont jamais modifiés par la recherche. Si aucun
mapping ne s'applique, le dossier peut recouper les candidats CoinGecko avec
le marché exact Bitvavo observé dans le catalogue : une seule paire CoinGecko
fraîche sur l'exchange `bitvavo`, avec même base et même cotation, établit une
résolution `resolved_cross_referenced`. Les preuves, leurs sources et dates
sont enregistrées dans le dossier; plusieurs correspondances ou une preuve
insuffisante laissent l'identité non résolue. Les noms et symboles seuls ne
valident jamais une identité, et le modèle ne peut pas le faire par sa seule
affirmation. Une référence ne certifie pas
l'interprétation libre. Les faits cités sont rendus par le programme depuis
son registre horodaté; les textes du modèle sont marqués comme non certifiés.
Chaque tour exécute au plus un appel structuré. Si Ollama en demande plusieurs,
le programme trace l'ensemble et ne traite que le premier; les autres sont
rejetés sans exécution.

Un succès d'outil signifie que l'opération s'est exécutée; il ne signifie pas
qu'une identité a été résolue ni que des faits ont été obtenus. Le rapport
sépare `execution_status`, `identity_status`, `information_result`, les
références de faits nouvellement acquises et les champs indisponibles. Après
un enrichissement non résolu, le modèle peut choisir une analyse historique
du marché Bitvavo ou terminer la recherche.

Les outils d'actualité sont optionnels et ne sont jamais appelés
systématiquement. La configuration RSS est donnée par `--news-sources` (défaut
`data/news-sources.json`) ; le modèle ne peut ni fournir ni modifier une URL.
La fenêtre de publication est de 30 jours par défaut et se règle avec
`--news-window-days` (1–365). `get_market_news` est du contexte général et ne
crée aucun lien avec le marché candidat. `get_asset_news` exige une identité
vérifiée et expose explicitement la force de l'attribution. Une collecte récente
n'implique pas un article récent ; aucun élément dans les flux consultés ne
prouve pas l'absence d'événement. Ollama reçoit un index court avec références,
éditeur, dates, portée, pertinence et fraîcheur ; les détails complets restent
dans le registre du rapport.
Les sources vérifiées, leurs conditions d'accès, les champs conservés et les
limites XML figurent dans [news-rss-sources.md](news-rss-sources.md). Ces
publications restent des affirmations de leurs éditeurs, distinctes des
observations de marché.

La fin de recherche est en deux phases. `finish_research` ne porte que les
références vérifiées à inclure. Le programme retire ensuite les outils, envoie
un registre compact des preuves et demande un JSON de conclusion avec état
`complete`, `partial` ou `abstain`. Le validateur revérifie toutes les
références. `finish_research` distingue `required_missing_refs`, qui sont les
références manquantes bloquantes pour le périmètre déclaré, de `missing_refs`,
qui restent visibles comme limites facultatives. Le validateur exige que les
références bloquantes soient un sous-ensemble des références manquantes. Le
statut décrit l'achèvement de la recherche, pas une décision de trading : une
absence de capitalisation ou de TVL ne bloque donc pas automatiquement une
analyse historique. Si une exigence est explicitement bloquante, `complete`
n'est pas permis.

ETH reste non résolu tant qu'aucun mapping explicite ou recoupement exact du
marché Bitvavo n'apporte de preuve. `no_explicit_verified_mapping` indique
qu'aucun mapping local n'existe; le modèle ne peut pas la remplacer par une
correspondance fondée sur le symbole.

## Budgets et traçabilité

Les options `--max-candidates` (1–5), `--max-tool-calls` (1–10), `--timeout`
(1–300 secondes), `--num-predict`, `--total-num-predict`,
`--conclusion-num-predict`, `--conclusion-timeout`, `--max-response-bytes`,
`--llm-timeout`, `--num-ctx` et `--context-safety-margin-tokens` bornent les ressources. Le budget total de
génération doit couvrir au moins un tour de recherche plus la conclusion.
Les tokens et le temps de conclusion sont réservés dès le départ; la recherche
s'arrête avant de les consommer.
Le budget total comprend catalogue, attente de service, appels d'outil et tours
Ollama. Les réponses externes et les appels de génération sont plafonnés.
Le snapshot initial du catalogue utilise trois requêtes HTTP Bitvavo; les
consultations des pages suivantes lisent ce snapshot en mémoire et ne refont
pas la collecte.
Un appel identique n'est pas répété sans changement de données ou reprise
autorisée par le code. Un budget épuisé ou une erreur produit un rapport
partiel avec la raison, les appels, les erreurs et les résultats déjà
conservés. Chaque requête Ollama est reconstruite depuis un registre à
références stables; les diagnostics et réponses brutes des outils ne sont pas
réinjectés dans les tours suivants. Les pages consultées sont transmises avec
leurs références `market_catalog:<marché>:<métrique>` et les pages non
consultées restent indiquées comme telles. Le programme ne réduit pas une liste
classée pour construire cette entrée.
Un estimateur calibré sur les comptes de
prompt Ollama observés, `ceil(JSON UTF-8 bytes / 4.5)`, budgète instructions,
schémas d'outils et
observations, puis ajoute les tokens de génération demandés et une marge de
sécurité. Si les pages consultées et le registre ne tiennent pas dans le
contexte, la recherche s'arrête avec `context_budget_exhausted`; elle ne
supprime pas des marchés consultés pour les remplacer silencieusement. Les
valeurs textuelles abrégées sont signalées par référence et restent complètes
dans le rapport.

Le rapport v7 distingue tokens réservés et tokens effectivement générés par
phase. Chaque tour conserve `think=false`, le compte d'évaluation du prompt,
les tokens générés, les octets, la durée, les outils demandés et les répétitions
détectées, l'estimation de prompt, la marge et les références abrégées. Si le JSON final échoue, le rapport produit un résumé déterministe
des outils, preuves, manques et erreurs, sans fabriquer de classification.

Le JSON conserve les requêtes d'outil structurées, leur statut, les sources,
les horodatages, les références disponibles/manquantes et les compteurs de
budget. Il ne conserve pas le raisonnement interne détaillé. Les descriptions
et autres contenus externes sont des données non fiables et ne sont jamais
traités comme des instructions. Dans la conclusion, des groupes de références
code-identifiés séparent mesures de marché, contenus d'actualité rapportés,
reprises, fondamentaux et manques. Cette séparation ne certifie pas le texte
libre de l'interprétation.

Cette fonctionnalité n'exécute que les outils de lecture listés ci-dessus.
Elle ne passe aucun ordre, ne modifie pas de portefeuille et ne donne aucune
autorisation d'agir sur les prix actuels. Une analyse historique reste
descriptive des fenêtres qu'elle rapporte.

## Revue contradictoire d'un rapport existant

`review-research` relit uniquement les références sélectionnées dans un rapport
`autonomous-research.*` et son `fact_ledger` / `missing_ledger`. Il ne relance
ni scan, ni analyse historique, ni enrichissement, ni collecte RSS/GNews. Le
rapport source reste intact; le JSON de revue doit être un nouveau fichier.

```bash
crypto-market-agents review-research \
  --input-json reports/autonomous-research-existing.json \
  --model qwen3:8b \
  --timeout 60 --num-predict 900 --num-ctx 8192 \
  --json reports/research-review-YYYYMMDD-HHMMSS.json
```

Le rôle est distinct : Ollama doit chercher à la fois les preuves qui
soutiennent et celles qui fragilisent la thèse, sans devoir reproduire la
conclusion ou la stratégie initiale. Le code calcule les relations numériques
exactes et transmet des observations identifiées par référence, métrique,
valeur, unité, horizon, disponibilité, fraîcheur et relations calculées. Le
contrat `numeric-relations.v1` compare zéro à zéro et un à la valeur 1 dans
l'unité affichée; ces comparaisons ne déterminent jamais le rôle favorable,
défavorable ou incertain. Le modèle choisit les observations, fournit un rôle
et une justification libre courte. Le code vérifie la référence et la
compatibilité de l'observation; l'interprétation reste non certifiée.

Les arguments rejetés et acceptés conservent leur index et leur référence; les
rejets exposent leur code d'erreur. Les arguments valides restent présents
dans un rapport `partial`, mais un rejet ramène le verdict
final à `insufficient`. Même sans rejet, le verdict demeure une appréciation
du modèle, et non une preuve sémantique certifiée. Les conditions d'invalidation
sont rendues comme hypothèses observables, jamais comme prédictions. Les
articles sont des affirmations de leur éditeur. Les absences limitent les
conclusions qui en dépendent mais ne constituent pas un défaut du projet.

La sortie `research-contradiction-review.v3` propose `supported`, `contested`
ou `insufficient`; ce ne sont ni des probabilités, ni des autorisations
financières. Une erreur, troncature ou contexte trop grand produit
`status=unavailable` sans verdict. Les arguments invalides isolés produisent
un rapport partiel avec diagnostics et verdict insuffisant. Le contexte est borné à 5 candidats
et 32 références par candidat; le défaut est un appel local, 900 tokens de
génération, 60 secondes, `think=false` et un contexte de 8192 tokens avec 512
tokens de marge. Aucun score de confiance n'est calculé.

## GNews : activation manuelle et facultative

GNews n'est pas déclaré à Ollama par défaut. Avant activation, la personne qui
lance la commande doit vérifier dans son compte que le plan utilisé est gratuit
et sans facturation automatique, puis fournir les deux options d'activation.
L'application ne peut pas lire l'état de facturation du compte et ne tente pas
de basculer vers un plan payant. La clé est lue depuis `GNEWS_API_KEY` (variable
du processus, ou `.env` / `--gnews-env-file` quand l'option est activée); elle
est envoyée dans l'en-tête HTTP et ne figure ni dans l'URL, ni dans les
événements ou rapports.

```bash
crypto-market-agents research \
  --objective "Examiner les nouvelles récentes pertinentes" \
  --enable-gnews --confirm-gnews-free-plan \
  --gnews-max-requests-per-research 5 \
  --gnews-max-requests-per-day 20 \
  --json reports/autonomous-research-gnews-YYYYMMDD-HHMMSS.json
```

Les plafonds sont strictement bornés à 5 requêtes par recherche et 20 appels
réseau par jour UTC. Le compteur journalier est persisté dans
`.cache/crypto-market-agents/gnews/daily-quota.sqlite3`; les réponses réussies
sont mises en cache sous `.cache/crypto-market-agents/gnews/http`. Une demande
qui utilise le cache compte dans le plafond de recherche, mais pas dans le
compteur d'appels réseau. Les tentatives réseau échouées consomment le compteur
journalier; les retries HTTP sont désactivés. Le cache n'inclut aucun secret.
La fenêtre est bornée à 30 jours et les extraits à 250 caractères. Aucune page
d'article n'est ouverte.

Le contexte indique le retard d'environ 12 heures associé au plan gratuit et
la couverture partielle de l'index; cela ne signifie pas que chaque résultat a
exactement ce retard. GNews ne garantit pas l'exhaustivité et un résultat nul
signifie seulement qu'aucun résultat pertinent n'a été retenu dans cette
recherche. La commande ne peut vérifier le plan ni les quotas externes du
compte. Le test réel précédent était cohérent avec les limites de texte du
plan gratuit, mais l'état réel du compte n'avait pas été vérifié; l'activation
nécessite donc cette confirmation manuelle.

`get_gnews_market_news` apporte seulement du contexte crypto général.
`get_gnews_asset_news` exige une identité résolue par le code et recherche par
nom fournisseur vérifié, jamais par symbole seul. Le code classe les résultats
comme pertinents retenus, ambigus, promotionnels ou hors sujet. Les derniers
restent traçables dans le rapport, mais ne deviennent pas des preuves
transmises à la conclusion. Les URL et textes sont des données externes non
fiables. Les reprises sont dédupliquées par URL ou titre et éditeur, et les
éditeurs observés sont comptés; plusieurs lignes d'un même éditeur ne valent
pas plusieurs sources indépendantes. Aucun résultat GNews, même retenu, ne
permet à lui seul une opération financière.

Les réponses du test réel précédent servent de régressions locales. Elles
montraient notamment que les dix résultats PEPE provenaient d'un seul éditeur,
que des articles sur Pepeto ou un prétendu cofondateur pouvaient être pris à
tort pour des nouvelles du token PEPE, et qu'un article était repris dans les
recherches générale et PEPE. Le filtre traite Pepeto/cofondateur comme ambigu,
exclut le contenu promotionnel et distingue une affirmation explicitement
centrée sur le mouvement du token. Cela contrôle la pertinence textuelle, pas
la véracité de l'article.

## Cycle de proposition multi-actifs, sans exécution

`proposal-cycle` charge des rapports `research` et `review-research` déjà
produits, ainsi qu'un état `paper-multi` existant. Il ne relance aucune collecte
fondamentale ou actualité et ne modifie pas le portefeuille. La revue doit
contenir l'empreinte SHA-256 exacte du rapport de recherche fourni.

Pour un nouveau portefeuille compatible avec les reçus d'exécution, initialiser
explicitement le format v2 avant de produire le plan :

```bash
crypto-market-agents paper-multi init --state paper/multiasset-v2.json
```

```bash
crypto-market-agents proposal-cycle \
  --research-json reports/autonomous-research.json \
  --review-json reports/research-review.json \
  --state paper/multiasset-v2.json \
  --model qwen3:8b \
  --timeout 60 --num-predict 800 --tracking-num-predict 384 \
  --max-tracked-positions 10 --total-num-predict 8192 --num-ctx 8192 \
  --json reports/proposal-cycle.json
```

La commande transmet à Ollama un contexte compact de liquidités, positions,
limites versionnées, références de recherche et arguments acceptés par la
revue. Elle effectue un appel indépendant par marché avec un schéma portant
un seul objet et un seul marché. Une ancienne réponse en liste ou comportant
plusieurs actions est invalide; aucune action n'est choisie arbitrairement.
Les références et marchés sont contrôlés contre ces rapports; les
interprétations et conditions d'invalidation rédigées librement restent des
hypothèses non certifiées. Le modèle ne fournit aucune taille. Le code plafonne
les budgets d'entrée par les limites de liquidités, d'opération, de position et
d'exposition du portefeuille. Une nouvelle entrée exige en outre des preuves
fraîches appartenant à ses dépendances, aucune référence requise manquante et
une revue `supported` sans rejet pour le candidat. Un rapport `partial` peut
rester utilisable si ces dépendances précises sont présentes. Une revue
`insufficient`, une référence étrangère au rapport ou une preuve périmée
entraîne un rejet de la proposition d'entrée.

`proposal-cycle` reste en lecture seule. L'exécution fictive est une commande
séparée et explicite, qui relit les deux rapports source et le plan sans
appeler Ollama ni collecter de nouvelles données :

Seuls les rapports `paper-proposal-cycle.v3` produits avec leur empreinte de
plan peuvent être exécutés; les rapports antérieurs v1/v2 restent conservés,
mais ne sont pas acceptés par cette commande.

```bash
crypto-market-agents execute-proposal-cycle \
  --plan-json reports/proposal-cycle.json \
  --research-json reports/autonomous-research.json \
  --review-json reports/research-review.json \
  --state paper/multiasset-v2.json \
  --json reports/proposal-cycle-execution.json
```

Seuls les plans marqués `accepted` peuvent être réexaminés. Sous verrou, le
programme vérifie l'empreinte exacte du portefeuille, les empreintes des deux
rapports, le hash du plan, la version de politique, la fraîcheur des preuves
et la fraîcheur des cotations déjà incluses dans le plan. Une cotation expirée
ou un portefeuille modifié rejette le plan; le code ne recalcule pas une
nouvelle décision à partir d'un prix différent. Les mêmes frais, minimums,
liquidités et expositions sont ensuite recalculés par le PaperEngine sur une
copie temporaire, dans l'ordre de plans consigné.

Les fills fictifs et le reçu idempotent du cycle sont écrits ensemble dans le
fichier du portefeuille par remplacement atomique. Le même cycle avec le même
contenu retourne son reçu sans nouveau fill; le même identifiant avec un
contenu différent est rejeté. Une exécution sans opération acceptée écrit un
rapport et un reçu de non-opération, sans entrée au journal des transactions.
Cette commande ne peut contacter aucun endpoint d'exchange et n'émet aucun
ordre réel.

Le format portefeuille v2 ajoute le journal des reçus. Les portefeuilles v1
restent lisibles et ne sont pas migrés automatiquement. Pour migrer un v1,
choisir explicitement :

```bash
crypto-market-agents paper-multi upgrade-for-proposal-execution \
  --state paper/multiasset-v1.json
```

La commande conserve une sauvegarde exclusive `.v1-backup`; un ancien rapport
de plan lié à l'état v1 ne correspondra pas à l'empreinte du portefeuille
migré et doit être recalculé avant toute exécution.

## Orchestration d'un cycle automatique fictif

`automatic-paper-cycle` enchaîne les commandes existantes dans cet ordre :
`research`, `review-research`, `proposal-cycle`, puis, seulement si
`--enable-paper-execution` est fourni, `execute-proposal-cycle`. L'exécution
fictive est désactivée par défaut. Ce chemin ne possède aucun client d'ordre
réel ni accès authentifié à Bitvavo. GNews reste inactif par défaut; son
activation exige `--enable-gnews --confirm-gnews-free-plan`, après vérification
manuelle du plan dans le compte. Le client garde également son quota persistant.
L'étape `research` utilise le catalogue paginé non classé décrit plus haut;
l'orchestrateur ne lance pas la commande facultative `scan` et conserve les
plafonds existants de génération, d'appels, de contexte et de durée.

Exemple sur un portefeuille v2 existant :

```bash
crypto-market-agents automatic-paper-cycle \
  --state paper/demo-v2.json \
  --reports-dir reports \
  --objective "Évaluer les candidats avec les preuves disponibles" \
  --model qwen3:8b --mode once \
  --cycle-timeout 600 --cycle-generation-tokens 12000 \
  --cycle-llm-calls 20 --cycle-http-requests 100 \
  --daily-generation-tokens 30000 --daily-http-requests 500
```

Pour autoriser explicitement l'exécution fictive :

```bash
crypto-market-agents automatic-paper-cycle \
  --state paper/demo-v2.json --reports-dir reports \
  --objective "Évaluer les candidats avec les preuves disponibles" \
  --model qwen3:8b --mode once --enable-paper-execution
```

Chaque cycle reçoit un UUID et un nouveau dossier
`reports/automatic-cycles/<uuid>/` contenant les rapports de chaque étape et
`cycle.json`. Les fichiers de rapport sont créés exclusivement; un dossier
existant n'est pas réutilisé pour un nouveau cycle. Le manifeste conserve les
empreintes SHA-256, le statut, les budgets réservés/consommés et les issues.
`--resume <dossier>` reprend uniquement les étapes dont la sortie n'a pas été
validée. Les rapports validés sont vérifiés par empreinte et réutilisés. La
configuration (objectif, modèle, activation et budgets) doit être identique.
Une étape d'exécution incertaine est répétée via la même commande idempotente;
le reçu du PaperEngine empêche un second fill. Si le plan a vieilli, le moteur
le rejette et le cycle se termine; la décision doit être refaite dans un nouvel
UUID.

Exemple de reprise d'un cycle initialement autorisé à exécuter fictivement
(reprendre les mêmes budgets et le même objectif que la commande d'origine) :

```bash
crypto-market-agents automatic-paper-cycle \
  --state paper/demo-v2.json --reports-dir reports \
  --objective "Évaluer les candidats avec les preuves disponibles" \
  --model qwen3:8b --mode once --enable-paper-execution \
  --resume reports/automatic-cycles/<uuid>
```

Un verrou de cycle par fichier de portefeuille empêche deux orchestrateurs
concurrents, sans garder le verrou comptable pendant les appels réseau/LLM.
Le portefeuille est contrôlé avant et après la proposition; le commit conserve
ses contrôles de révision et de fraîcheur sous son verrou comptable. Les états
finaux différencient les erreurs techniques, les rapports partiels, la revue
indisponible, l'abstention, la proposition non admissible, l'expiration d'un
plan et l'exécution fictive. Le succès technique d'une étape ne signifie pas
qu'elle a obtenu une information pertinente ou proposé une opération.

`--mode repeat --interval-seconds 3600` répète les cycles au premier plan.
`--stop-file /chemin/stop` interdit le cycle suivant une fois le fichier créé;
une étape déjà lancée se termine avant l'arrêt. Ctrl+C interrompt le cycle et
le reçu du portefeuille est consulté pour établir si le commit a eu lieu.
L'arrêt suspend aussi la surveillance locale. Il ne liquide jamais les
positions.

Les budgets par défaut sont répartis ainsi. Les réserves de conclusion sont
incluses dans les plafonds `research`, pas ajoutées une seconde fois.

### Mode autonome « suivi seul »

`automatic-paper-cycle --tracking-only --mode once` suit les positions
ouvertes sans lancer le scanner, la recherche de candidats ni une revue de
découverte. Le rapport d'historique est produit par `position-history` : il
réutilise d'abord les analyses Bitvavo encore fraîches et vérifiées par
empreinte dans les dossiers de cycles; sinon, il collecte au plus 1 440
bougies horaires par position (dix positions au maximum). Les échecs et
historiques insuffisants restent visibles et rendent le suivi incomplet.

Chaque position est évaluée séparément à partir de son marché, de ses
observations et de la thèse d'entrée uniquement si celle-ci est retrouvée dans
un cycle exécuté vérifiable. L'absence de thèse n'est pas comblée. Le modèle
peut proposer `hold`, `reduce`, `close` ou `abstain`; une panne ou des preuves
insuffisantes donnent un suivi incomplet, jamais une décision implicite de
conservation. Les règles du PaperEngine, la fraîcheur, les verrous et les
reçus idempotents restent applicables. L'exécution fictive demeure désactivée
sans `--enable-paper-execution`.

Un portefeuille sans positions ouvertes termine en `no_open_positions` sans
requête réseau, appel LLM ni réservation au compteur journalier. Exemple en
lecture seule :

```bash
crypto-market-agents automatic-paper-cycle \
  --state paper/demo-v2.json --reports-dir reports \
  --objective "Suivre les positions fictives ouvertes" \
  --model qwen3:8b --mode once --tracking-only
```

Le mode partage exactement le registre quotidien des cycles complets. Pour
les dix positions configurées au maximum, il réserve au plus 90 secondes et
10 requêtes HTTP pour l'historique; puis 400 secondes, 10 appels LLM, 3 840
tokens générés, 10 invocations externes et 20 tentatives HTTP pour
propositions et cotations; l'exécution fictive, si activée, ajoute 10 secondes
et aucun appel réseau/LLM. L'orchestrateur réserve selon le nombre réel de
positions ouvertes, sans dépasser ces maxima. Le total au pire cas est donc
500 secondes avec exécution activée (490 sans), 10 appels LLM, 3 840 tokens,
10 invocations externes et 30 tentatives HTTP. Ces valeurs sont prises sur les
mêmes plafonds journaliers des cycles complets, sans quota additionnel.

Aux quotas journaliers actuels, un seul suivi seul au pire cas par jour est une
cadence pilote prudente si le même jour peut aussi accueillir un cycle
complet : les budgets consommés par ce dernier réduisent le reliquat partagé.
Un suivi seul maximal isolé consomme 3 840 des 30 000 tokens, 10 des 60 appels
LLM, 30 des 500 tentatives HTTP et jusqu'à 500 des 3 600 secondes. Il n'est
pas lancé en boucle par cette commande; `--mode repeat` reste au premier plan
et n'est pas recommandé pour le pilote tant que les consommations réelles
n'ont pas été mesurées.

| Étape | Durée maximale | Génération maximale | Appels LLM réservés | Requêtes HTTP réservées |
|---|---:|---:|---:|---:|
| Research et conclusion | 300 s, dont 45 s réservées à la conclusion | 6 000 tokens, dont 600 pour la conclusion; 1 200 par tour | 12 (jusqu'à 10 outils, puis appels de contrôle/conclusion) | 80 |
| Review | 60 s | 900 tokens, un appel | 1 | 0 |
| Proposal | 400 s, appels Ollama, historique et cotations dans le même plafond | 8 192 tokens au total : 800 × 5 marchés de découverte + 384 × 10 positions suivies au maximum | 15 (5 découverte + 10 suivi) | 40 (jusqu'à 15 marchés cotés, deux requêtes par marché, plus 10 collectes historiques) |
| Exécution PaperEngine | 10 s réservées, sans appel réseau ni LLM | 0 | 0 | 0 |
| **Plafond cycle** | **800 s** | **16 000 tokens** | **30** | **120** |

Le maximum configuré des étapes est 15 092 tokens de génération, 28 appels
LLM, 25 invocations d'outils/cotations et 120 tentatives HTTP; ces maxima
tiennent dans les plafonds du cycle. La création d'une configuration dont les
plafonds ne financent pas les étapes déclarées est rejetée avant de commencer.
Le budget de proposition est partagé entre temps modèle et récupération des
cotations; le rapport conserve le timeout effectif de chaque appel.
Le plafond de génération par cycle par défaut est explicitement fixé à 16 000
tokens. L'ancien plafond de 12 000 ne finance pas le maximum des étapes par
défaut (6 000 + 900 + 8 192 = 15 092) et est rejeté lors du précontrôle; il
n'est pas augmenté silencieusement par l'orchestrateur. L'exécution PaperEngine
ne réserve aucun token.

Les options `--research-*`, `--review-*`, `--proposal-*`, `--cycle-*` et
`--daily-*` rendent ces valeurs configurables. Les budgets sont réservés avant
chaque étape et rapprochés des consommations. `http_requests` compte les
tentatives réseau, retries inclus et caches exclus, dans le helper HTTP partagé
et les clients Bitvavo directs utilisés par le catalogue, le scanner
facultatif, l'historique et les cotations. Les appels locaux Ollama sont comptés séparément dans les budgets
LLM. Les autres accès directs non instrumentés ne sont pas comptabilisés.
`external_tool_calls` est un compteur séparé des outils de recherche et des
observations de cotation. Les clients conservent aussi leurs propres délais,
caches et quotas; GNews reste inactif par défaut et son quota gratuit est
persistant. Le registre journalier est conservateur si une interruption laisse
un artefact dont le nombre exact de requêtes ne peut pas être reconstitué; si
une étape échoue après des tentatives mesurées, ce nombre est immédiatement
enregistré dans le manifeste et le quota journalier.
Le compteur journalier est partagé entre les portefeuilles du même utilisateur
et conservé dans `~/.local/state/crypto-market-agents/automatic-cycle-daily.json`;
il ne repart pas de zéro en changeant de portefeuille ou de dossier de rapports.
Les quotas journaliers par défaut sont 3 600 secondes, 60 appels LLM, 30 000
tokens générés, 100 appels d'outils externes et 500 tentatives HTTP. Un cycle
au pire de ses budgets utilise 770 secondes, 28 appels LLM, 15 092 tokens,
25 appels d'outils et 120 tentatives HTTP. Le quota de génération permet un
seul cycle complet entièrement au pire cas (deux demanderaient 30 184 tokens); des
consommations réelles inférieures sont rapprochées et peuvent laisser du budget
pour d'autres cycles. Le quota GNews distinct reste désactivé sans activation
explicite.

Pour un pilote, limiter le démarrage à un cycle complet par jour, ou à un
suivi seul distinct si le suivi est prioritaire. Les deux consomment le même
registre quotidien : les coûts réels du premier réduisent le budget du second.
Le suivi seul maximal isolé peut s'exécuter jusqu'à sept fois selon les plafonds
de génération (30 000 / 3 840), mais cette division ne réserve aucune marge aux
cycles complets, aux durées réelles ou aux erreurs réseau; elle ne constitue
pas une recommandation de cadence. Commencer par une exécution manuelle
quotidienne, mesurer les consommations et réduire la fréquence si des cycles
complets doivent aussi tourner. Ces recommandations ne démarrent ni n'activent
un pilote.

Le mode repeat est un processus au premier plan uniquement; aucun service
illimité en arrière-plan n'est installé ni lancé par cette commande.

Le moteur multi-actifs évalue les plans sur une unique copie jetable du
portefeuille pour appliquer ses règles existantes, notamment frais,
glissement, spread, minimums, pas de quantité, budget partagé et exposition
courante. L'ordre est déterministe : réductions, achats, conservation,
abstentions, puis marché par ordre lexical dans chaque groupe. Chaque plan
accepté met à jour cette copie avant l'évaluation du suivant, ce qui réserve
cumulativement liquidités et exposition. Le rapport consigne cet ordre et le
budget réservé par achat. Les plans de réduction
ne nécessitent pas de nouvelle thèse d'achat et ne sont pas bloqués par une
cotation manquante d'une autre position; le marché réduit doit toutefois avoir
une cotation valide et une identité de position non ambiguë. Une position
ouverte est suivie indépendamment de la présélection du scanner : les marchés
déjà détenus ne sont pas écartés parce qu'ils ne font plus partie des meilleurs
candidats. Les métriques historiques Bitvavo fraîches déjà présentes dans le
registre sont réutilisées. Si elles manquent ou sont périmées, une collecte
historique indépendante est tentée pour chaque position suivie, même si
research, la découverte ou la revue a échoué : une requête publique de bougies
1h, limitée à 1 440 bougies par position (dix positions au maximum). L'échec ou
l'épuisement du temps est consigné et le suivi reste incomplet; cela ne crée
pas une thèse d'entrée. Le budget de découverte couvre au plus cinq marchés; un budget
distinct couvre jusqu'à dix positions ouvertes, avec 384 tokens par appel de
suivi contre 800 par appel de découverte. Le plafond cumulé est configurable
mais ne dépasse pas 8192 tokens par commande `proposal-cycle`; une position
au-delà du plafond de suivi est signalée non évaluée, jamais implicitement
conservée. Les appels par position demandent `hold`, `reduce`, `close` ou
`abstain`; `close` vend la position entière sous les mêmes contraintes
existantes de marché, frais et minimums, sans seuil de vente supplémentaire.
Les cotations nécessaires sont bornées à quinze marchés, avec 30 requêtes HTTP
réservées au maximum; dix requêtes supplémentaires sont réservées aux collectes
historiques de position, pour un plafond proposal de 40 requêtes. Si une position ne
peut être valorisée, le rapport est incomplet. Une cotation fraîche de la
position autorise l'évaluation d'une réduction même si une autre position est
non valorisable; les nouvelles entrées restent soumises à la vérification
globale de l'exposition.

Les preuves de la thèse d'entrée ne sont reprises que si un achat du journal
du portefeuille se relie à un cycle automatique antérieur dont les artefacts
proposal/execution et empreintes sont vérifiés sous le répertoire de rapports
configuré. La thèse est affichée comme historique, jamais comme preuve
actuelle. Pour les achats anciens, directs, ou dont le rapport source n'est
plus présent/intègre, la thèse est indiquée absente et n'est pas reconstruite.
Les pannes du modèle, preuves manquantes, revues insuffisantes et cotations
périmées produisent un suivi incomplet ou non évalué, pas une décision de
conservation. Une baisse de prix seule n'est pas un motif suffisant
d'invalidation. Le dashboard affiche le dernier état de suivi par position
et différencie une suggestion non validée d'une action examinée.
Si research échoue sans artefact, l'orchestrateur conserve un rapport explicite
`unavailable` sans faits, puis poursuit review/proposal pour tenter le suivi
des positions. Une revue indisponible n'arrête pas proposal. Dans ces cas,
l'absence de candidat ou de revue rend le suivi incomplet; une éventuelle
suggestion `hold`, `reduce` ou `close` n'est pas convertie en plan exécutable.
Les faits du rapport source visant le marché détenu sont transmis même quand
celui-ci n'est plus présélectionné, mais restent non évalués si la revue manque.

Le cycle est borné à 400 secondes pour proposal. `--num-predict` borne chaque
appel de découverte (défaut 800, maximum 1200), `--tracking-num-predict` chaque
appel de suivi (défaut 384, maximum 1200), `--max-tracked-positions` le nombre
de positions suivies (défaut 10, maximum 10), et `--total-num-predict` la
génération cumulée (défaut 8192, maximum 8192). Les budgets doivent financer
les deux catégories au pire de leurs plafonds configurés, sinon la commande
est rejetée. Dans l'orchestrateur, les options `--proposal-*` portent ces
limites. Les rapports d'anciens cycles `paper-proposal-cycle.v2` restent
consultables mais ne peuvent pas être repris comme rapports v3 pour une
exécution ou un suivi actualisé.

Le rapport distingue statut technique, validité des réponses du modèle,
admissibilité des plans et décision; un rejet de réponse ne devient jamais une
abstention volontaire. `think=false`, les budgets, l'estimation de contexte,
les compteurs de génération, les empreintes des rapports, les cotations et
leurs motifs sont consignés. L'heure
Bitvavo est l'heure locale de réception; le ticker consulté ne fournit pas
l'heure de transaction. Aucun plan ne crée de transaction ou d'ordre réel.

Les rapports d'entrée doivent dater de moins de 24 heures. Les observations
retenues pour une nouvelle entrée sont évaluées selon leur source : catalogue
Bitvavo 1 heure, historique Bitvavo 6 heures, actualités 24 heures et
fondamentaux CoinGecko/DefiLlama 7 jours. Une source non reconnue par cette
politique bloque l'entrée jusqu'à l'ajout documenté d'une règle de fraîcheur.
Le contexte par défaut est de 8192 tokens, avec 512 tokens de marge. Le rapport
de plan est séparé et n'écrase pas les
rapports précédents.
