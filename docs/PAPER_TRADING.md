# Portefeuille simulé et décision IA

Cette version ajoute un laboratoire de trading **simulé** à l'application
existante. Elle ne passe aucun ordre réel, ne demande aucune clé d'exchange et
n'utilise pas le solde de votre compte Crypto.com ou Bitvavo.

## Démonstration reproductible

Depuis la racine du dépôt, après installation (`pip install -e '.[dev]'`) :

```bash
crypto-market-agents paper init --state paper/demo.json
crypto-market-agents paper run --state paper/demo.json --mock --runs 3 --interval-seconds 0
crypto-market-agents paper status --state paper/demo.json
crypto-market-agents dashboard --reports-dir reports
```

Sans installation du point d'entrée, utiliser
`PYTHONPATH=src python3 -m crypto_market_agents.cli` à sa place.

La démonstration utilise des cours fictifs et le décideur `rules`. Celui-ci est
une stratégie témoin déterministe, **pas un modèle IA**. Les trois premières
observations montrent une attente, un achat et une vente à perte, frais inclus.
Cela teste le fonctionnement ; cela ne mesure pas une rentabilité.

`init` refuse d'écraser un portefeuille existant. Pour continuer, relancer
uniquement `run`. Pour une autre expérience, choisir un nouveau chemin `--state`.
Les fichiers `paper/` et les exports `reports/` sont ignorés par Git.

## Page de suivi en direct

Dans un premier terminal, ouvrir le tableau de bord sur le fichier du portefeuille
à suivre (le même `--state` que pour `paper run`) :

```bash
crypto-market-agents dashboard --state paper/bitvavo.json
```

Afficher **http://127.0.0.1:8000** dans le navigateur. La page se met à jour toutes
les **2 secondes**, sans rechargement : valeur du portefeuille, euros disponibles,
position, résultat, frais, courbe du capital et 100 dernières décisions avec leur
justification. Les anciens rapports restent accessibles via « Rapports ».

Le grand graphique propose deux vues : **Marché**, pour les cours effectivement
observés au fil des cycles, et **Portefeuille**, pour sa valorisation comparée au
capital initial. Le cours du marché et le résultat du portefeuille sont des
mesures distinctes. Le terminal **Vector**, bleu nuit et cyan, place le graphique
au centre, le marché suivi et la répartition à gauche, l'état de l'IA et la
discussion à droite. Les indicateurs et le journal apparaissent sous le graphique.
La courbe utilise les cotations enregistrées : aucun chandelier, volume ou fil
d'actualités fictif n'est ajouté pour remplir l'interface.

Dans un second terminal, démarrer la simulation sur ce portefeuille déjà initialisé :

```bash
crypto-market-agents paper run --state paper/bitvavo.json \
  --provider ollama --model NOM_MODELE_LOCAL --runs 0 --interval-seconds 300
```

Le modèle doit être installé et Ollama lancé. `--runs 0` poursuit la simulation
jusqu'à `Ctrl+C`. Ici le moteur attend **5 minutes entre deux cycles** ; rafraîchir
la page ne déclenche ni nouvelle décision ni appel réseau vers l'exchange.
Pour une démonstration hors ligne, remplacer le fichier par `paper/demo.json`
et lancer `paper run --state paper/demo.json --mock --runs 0 --interval-seconds 10`.
Ce mode utilise les règles de référence, sans IA.

La page indique l'étape réelle du moteur : récupération des cours, réflexion,
contrôle de la décision, attente du prochain cycle ou arrêt. Un signal d'activité
est enregistré toutes les 5 secondes ; après 20 secondes sans signal, l'activité
n'est plus présentée comme confirmée. Les erreurs restent visibles jusqu'au
prochain cycle réussi. Un seul moteur peut fonctionner sur un même portefeuille.

Les cours et la valorisation datent du **dernier cycle enregistré**, pas de chaque
rafraîchissement de la page. Leur âge est affiché ; le dépassement du seuil de
fraîcheur de 120 secondes est normal entre deux cycles espacés de 5 minutes.
Pendant une décision, la page conserve le dernier état validé et reste réactive.
Un portefeuille absent ou corrompu affiche une erreur et n'est jamais réinitialisé.

Le suivi est en lecture seule, ne contient pas de bouton d'ordre et écoute sur
`127.0.0.1` par défaut. Il est destiné à un usage local, sans authentification.
Le fichier voisin `<state>.activity.json` contient l'activité temporaire du moteur,
distincte des soldes persistants. Arrêter le serveur de suivi ne stoppe pas le
moteur lancé dans l'autre terminal. Sans `--state`, la commande `dashboard`
conserve son accueil historique des rapports.

### Discuter avec l’agent

La discussion est directement intégrée à la colonne de droite. Le bouton
**Discuter avec l’agent** amène au champ de saisie, y compris sur mobile.
Par exemple : « Pourquoi tu n’as rien acheté ? », « Explique ta dernière
décision » ou « Où en est mon portefeuille ? ».

Le chat utilise le modèle local indiqué par le moteur lancé avec
`--provider ollama --model NOM_MODELE_LOCAL`. Il reçoit à chaque question les
soldes, les limites, la fraîcheur des cours et les cinq dernières décisions
enregistrées. Il explique ces observations en français ; une erreur ou un cours
ancien reste une erreur ou une observation datée. La discussion ne modifie ni
les soldes ni les paramètres de la simulation.

Les échanges sont conservés dans la session de cet onglet, séparément pour
chaque portefeuille, et peuvent être effacés avec **Effacer**. Seuls les derniers
échanges sont transmis au modèle pour limiter le temps de réponse. Le suivi
continue de se rafraîchir pendant la génération ; une seule réponse de chat est
préparée à la fois. En cas d’indisponibilité du modèle ou de délai dépassé, la
page affiche l’erreur et permet de réessayer.

La route `POST /api/chat` accepte seulement les requêtes locales en JSON depuis
le tableau de bord ou un client local. Le modèle est appelé sur
`127.0.0.1:11434`, sans proxy ni redirection ; aucun service de discussion distant
n’est utilisé. Une simulation avec le fournisseur `rules` ne dispose pas de
modèle de chat configuré.

## Simulation sur les cours publics de Bitvavo

```bash
crypto-market-agents paper init --state paper/bitvavo.json --source bitvavo --market BTC-EUR
crypto-market-agents paper run --state paper/bitvavo.json --runs 12 --interval-seconds 300
```

Ces commandes utilisent seulement deux endpoints publics GET : `/markets` et
`/ticker/book`. Elles ne nécessitent ni compte ni clé Bitvavo. Le marché doit être
ouvert, coté en euros et autoriser les ordres au marché. Les minimums en euros et
en quantité, ainsi que le pas de quantité, sont lus dans les règles du marché.

L'horodatage du ticker est celui de sa **réception locale**, car cet endpoint ne
fournit pas de date d'observation de l'exchange. Les prix expirent après 120 secondes
pour l'exécution simulée. Un même état ne peut mélanger cours fictifs et publics.

Sources du contrat API : [marchés](https://docs.bitvavo.com/docs/rest-api/get-markets/)
et [bid/ask](https://docs.bitvavo.com/docs/rest-api/get-ticker-book/).

## Brancher un véritable modèle IA local

Le connecteur accepte un modèle **déjà installé** dans Ollama, sur
`http://127.0.0.1:11434`. Il ne télécharge pas de modèle et ne lance pas de service.
Choisir un modèle local capable de produire des réponses structurées, adapté à la
mémoire de votre machine. Remplacer `NOM_MODELE_LOCAL` ci-dessous :

```bash
crypto-market-agents paper run --state paper/bitvavo.json \
  --provider ollama --model NOM_MODELE_LOCAL --runs 12 --interval-seconds 300
```

L'IA reçoit les soldes fictifs, les limites, la cotation courante et jusqu'à
48 observations précédentes. Elle propose une seule action `buy`, `sell` ou
`hold`, un montant en euros et une justification. Le modèle ne reçoit aucun outil
d'exécution ni clé de compte. Ses réponses sont validées puis contrôlées par le
moteur de risque. Une réponse invalide ou une IA indisponible produit une attente
explicite ; il n'y a pas de remplacement silencieux par une autre stratégie.

Le décideur de référence reste disponible avec `--provider rules` pour comparer
les comportements. Les modèles et règles ne sont pas calibrés pour générer un
bénéfice. Avec seulement un historique court, l'abstention est normale.

Contrat Ollama : [chat](https://docs.ollama.com/api/chat) et
[sorties structurées](https://docs.ollama.com/capabilities/structured-outputs).

## Réutiliser les analyses existantes

Un rapport JSON généré par `report` peut être fourni en contexte :

```bash
crypto-market-agents paper run --state paper/bitvavo.json \
  --provider ollama --model NOM_MODELE_LOCAL \
  --analysis-report reports/report_DATE_HEURE.json
```

Le rapport doit dater de moins d'une heure. Un rapport périmé/invalide empêche le
cycle ; un risque élevé/critique, une confiance inférieure à 0,5 ou un agent non
réussi bloque les achats. Les ventes et l'attente restent possibles. Les textes
du rapport sont traités comme des données, pas des instructions au modèle.
Sans ce paramètre, la décision s'appuie seulement sur les observations du marché
et du portefeuille ; les quatre agents historiques ne sont pas relancés.

## Limites fixées lors de l'initialisation

| Paramètre | Valeur initiale | Signification |
| --- | --- | --- |
| `--capital` | 50 € | Argent fictif, maximum 50 € au départ |
| `--max-trade` | 10 € | Budget maximum par décision |
| `--max-position` | 25 € | Exposition maximale autorisée lors d'un achat |
| `--max-loss` | 5 € | Repli depuis le sommet observé déclenchant l'arrêt des achats |
| `--fee-rate` | 0,0025 | Hypothèse de commission 0,25 % par exécution |
| `--slippage-bps` | 10 | Glissement défavorable de 0,10 % |

Les limites sont persistées et ne peuvent pas être changées par le modèle ou
par une reprise de `run`. Après déclenchement du seuil de perte, les nouveaux
achats restent bloqués pour ce portefeuille, même si le prix remonte. Ce seuil
n'est **pas une garantie de perte maximale** : il ne liquide pas automatiquement
la position et les cours peuvent continuer de baisser.

Les achats utilisent l'ask, les ventes le bid, avec glissement défavorable.
Le budget d'achat comprend les frais ; le montant de vente correspond à un
notionnel calculé au bid, plafonné par la quantité possédée. Les lots sont arrondis
vers le bas, les frais vers le haut. Aucun solde négatif, emprunt ou vente à
découvert n'est permis. Les minimums restent applicables à la sortie : une petite
position peut devenir invendable dans cette simulation après une forte baisse.

Le simulateur ne reproduit pas la profondeur du carnet, les exécutions partielles,
la priorité dans une file ou la latence d'un vrai ordre. Le taux de frais est une
hypothèse configurable, pas une lecture des frais personnels du compte.

## Journal, reprise et suivi

Les montants sont conservés avec `Decimal`, sérialisés comme chaînes JSON. Un
verrou interprocessus POSIX protège chaque lecture/décision/écriture et le commit
remplace atomiquement le fichier. Linux/macOS sont requis pour ce verrou.
Un état absent, corrompu ou incohérent n'est jamais réinitialisé automatiquement.

Pour rejouer sans doublon un cycle dont l'issue est incertaine :

```bash
crypto-market-agents paper run --state paper/demo.json --decision-id cycle-001
```

Le même identifiant renvoie le résultat déjà enregistré, sans nouvel appel marché
ou modèle. Sans `--decision-id`, chaque cycle reçoit un nouvel identifiant.
Les cycles échoués sont affichés et n'exécutent aucune opération ; une boucle
continue les cycles suivants et retourne un code non nul si une erreur a eu lieu.
`--runs 0` continue jusqu'à Ctrl+C avec un intervalle strictement positif.

Chaque exécution de `run` exporte JSON, Markdown et HTML avec les soldes, le
résultat réalisé/non réalisé, les frais, le repli maximal et les décisions.
Le dashboard existant les référence. `paper status --json` fournit les métriques
sans réseau. La valorisation utilise le dernier bid observé : elle n'est pas
rafraîchie par `status`.

Les comparaisons incluent conserver les euros et acheter/conserver l'actif dès
la première observation. Le second témoin utilise frais, glissement et lots,
mais investit tout le capital ; son exposition diffère donc du bot plafonné à
25 €. Les deux valorisent au bid sans frais d'une liquidation hypothétique.
Le coût du modèle, de l'électricité/hébergement et la fiscalité ne sont pas inclus.

## Avant toute éventuelle exécution réelle

Cette livraison ne contient pas d'adaptateur d'ordre réel. Les garde-fous
`TRADING_ENABLED=false`, `ORDER_EXECUTION_ENABLED=false`,
`WITHDRAWALS_ENABLED=false`, `EXCHANGE_MODE=disabled` restent en vigueur.
Il reste à évaluer la stratégie sur plusieurs périodes hors échantillon, puis à
concevoir la réconciliation des vrais ordres/soldes, les exécutions partielles,
les restrictions du compte et les tests de reprise de la plateforme retenue.
