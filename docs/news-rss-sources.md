# Recherche d'actualités RSS/Atom

La boucle autonome peut choisir `get_market_news` pour lire les flux généraux
configurés ou `get_asset_news` pour un actif dont l'identité a été résolue par
le code. Aucun flux n'est consulté systématiquement. Ce chemin public ne demande
ni clé ni service payant ; le client NewsAPI existant reste disponible pour ses
usages configurés.

## Flux initiaux et conditions d'accès vérifiées

Le fichier [`data/news-sources.json`](../data/news-sources.json) contient trois
flux officiels publics :

| Source | Flux | Usage annoncé par l'éditeur |
|---|---|---|
| U.S. Securities and Exchange Commission | `https://www.sec.gov/rss/news/press.xml` | La SEC propose ses flux pour ses contenus publics. L'accès automatisé demande un User-Agent déclaré, un usage efficient et un débit actuel limité à 10 requêtes/s : [page RSS](https://www.sec.gov/files/about/secrss.shtml), [politique d'accès EDGAR](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data). Le client fait des requêtes séquentielles bornées et identifie son logiciel/version. |
| Federal Reserve Board | `https://www.federalreserve.gov/feeds/press_all.xml` | La Fed destine ses flux aux lecteurs/agrégateurs affichant liens, titres et résumés. Ses informations sont publiques sauf mention contraire et doivent être attribuées : [page RSS](https://www.federalreserve.gov/feeds/feeds.htm), [disclaimer](https://www.federalreserve.gov/disclaimer.htm). |
| Documentation API Bitvavo | `https://docs.bitvavo.com/releases/rss.xml` (`bitvavo-api-release-notes`) | La page officielle « Releases » propose explicitement l'abonnement à son flux RSS pour les mises à jour API : [notes de version et lien RSS](https://docs.bitvavo.com/releases/). Le client ne consulte que le flux publié par cette documentation, de façon séquentielle, bornée et avec son User-Agent identifié. |

Cette sélection est volontairement réduite : annonces réglementaires et
macroéconomiques, plus changements API de Bitvavo. Elle n'est pas une revue
exhaustive de l'actualité crypto.
Les conditions d'accès et les URL peuvent évoluer ; vérifier les pages des
éditeurs avant d'ajouter une source.

### Flux officiels de projets et plateformes examinés

Le flux Bitvavo est activé en portée générale `market`, avec le domaine
`platform`. Il porte sur les changements de son API et de sa documentation,
pas sur les annonces des projets crypto ni sur des cotations de tokens. Il ne
permet pas d'établir qu'un actif annoncé ailleurs est disponible sur Bitvavo.
Une entrée reste l'affirmation officielle de Bitvavo sur sa propre API ; elle
n'est pas une confirmation indépendante. Cette source n'est pas associée à un
actif candidat.

Le collecteur classe certains articles selon le type de source, les catégories
du flux et quelques mots-clés de titre : `listing_announcement`,
`project_update`, `platform_update`, `incident`, `promotional_content` ou
`unclassified`. Le champ `content_category_basis` signale ce classement
heuristique. Il ne vérifie ni l'annonce ni sa portée réelle.

Le blog officiel de l'Ethereum Foundation annonce la prise en charge de lecteurs
RSS. Ses conditions couvrent explicitement `blog.ethereum.org` et interdisent
les robots ou autres accès automatisés pour suivre ou copier le contenu. Même si
les contenus non marqués sont sous CC BY 4.0, cette licence ne lève pas la
restriction d'accès automatisé : le flux a donc été examiné mais n'est pas
activé sans autorisation écrite ([annonce RSS](https://blog.ethereum.org/2022/08/31/blog-internationalization),
[conditions officielles](https://ethereum.org/terms-of-use/)).

Solana et Kraken publient des contenus officiels utiles, mais leurs conditions
interdisent respectivement les systèmes automatisés d'accès au site et les bots,
scripts ou extractions automatisées de leur contenu ; aucun de ces blogs n'est
activé. Uniswap n'a pas de flux RSS officiel confirmé par ses pages ; ses
conditions interdisent aussi le data mining et le scraping des produits. Nous
n'avons donc pas tenté de récupérer leurs pages d'articles ([Solana](https://solana.com/tos),
[Kraken](https://www.kraken.com/legal/global-terms),
[Uniswap](https://support.uniswap.org/hc/en-us/articles/30935100859661-Uniswap-Labs-Terms-of-Service)).

Les conditions officielles des projets restent à vérifier individuellement.
Les flux SEC/Federal Reserve ne couvrent que leurs publications réglementaires
et macroéconomiques ; le flux Bitvavo ne couvre que ses notes de version API.
Aucun de ces flux ne donne une couverture générale des annonces des projets ou
plateformes crypto. Les URL, accès et conditions peuvent évoluer.

Des médias connus n'ont pas été activés par défaut : Cointelegraph interdit
dans ses conditions l'usage de son contenu par des systèmes d'IA sans accord
écrit préalable ([conditions](https://cointelegraph.com/terms-and-privacy)) ;
CoinDesk interdit l'accès automatisé sans autorisation écrite, malgré sa page
qui recommande l'abonnement à son RSS ([RSS](https://www.coindesk.com/coindesk-news/2021/09/17/coindesk-rss),
[conditions](https://www.coindesk.com/terms)). CryptoSlate réserve ses flux à
la lecture personnelle non commerciale et exclut les accès automatisés sans
permission ([conditions de flux](https://cryptoslate.com/feeds/)). Ils ne sont
pas utilisés par défaut.

## Configuration

`research --news-sources chemin.json` accepte au plus dix flux. Chaque entrée
contient un identifiant local, l'éditeur, une URL HTTPS publique, un type de
source (`news_article`, `official_announcement`, `press_release` ou
`promotional_content`) et les périmètres (`market`, `asset`) autorisés. Le
modèle ne peut pas fournir ni modifier d'URL. Un flux réservé à un actif peut
porter `asset_ids`, contenant des identifiants CoinGecko exacts ; ne les ajouter
que si le flux est bien l'espace officiel de cet actif.
Le champ facultatif `coverage_domain` décrit la portée connue (`macro`,
`regulatory`, `market_general`, `project` ou `platform`). Il classe la source,
pas la pertinence d'un article pour un token.

Exemple d'entrée :

```json
{
  "id": "verified-project-blog",
  "publisher": "Project Foundation",
  "url": "https://project.example/feed.xml",
  "source_type": "official_announcement",
  "coverage_domain": "project",
  "scopes": ["market", "asset"],
  "asset_ids": ["provider-coin-id"]
}
```

## Attribution et portée des observations

Pour une requête par actif, l'identité doit être `resolved_explicit` ou
`resolved_cross_referenced`. Les noms Bitvavo/CoinGecko issus du dossier vérifié
peuvent servir de termes exacts ; le symbole seul n'est jamais utilisé. Une
correspondance exacte de nom dans un flux général est étiquetée comme possible,
pas comme certaine. Un flux officiellement configuré pour l'identifiant du
fournisseur permet une attribution au flux ; il ne prouve pas la véracité de ses
affirmations.

Chaque élément conserve un identifiant stable, titre, éditeur, URL, extrait
présent dans le flux, dates de publication et collecte, état de la date, type de
contenu et références de regroupement. Les dates inconnues ou invalides restent
nulles ; elles ne sont pas remplacées par l'heure de collecte. Seuls les
éléments RSS/Atom sont lus : aucun article ni lien externe contenu dans le flux
n'est ouvert. `is_full_article` reste faux.

Les URL canoniques identiques sont dédupliquées. Les titres proches sont
regroupés dans une fenêtre de trois jours quand les dates existent. Ce
regroupement indique une reprise possible, pas une confirmation indépendante.
Les types éditorial, annonce officielle, communiqué et promotion ne sont
distingués que d'après la configuration du flux et ses métadonnées/étiquettes.

Les champs d'actualité sont des affirmations publiées et des textes externes
non fiables. Le rapport v6 sépare les observations de marché, le contexte
général, les recherches d'actifs vérifiés, les contenus rapportés, les reprises,
les données fondamentales et les éléments manquants. Une référence `news:general`
ne désigne jamais une nouvelle attribuée à l'actif de la boucle. Cette séparation
par le code ne certifie pas le texte libre du modèle ni la véracité de la
publication.

## Bornes techniques

- HTTPS uniquement, dix flux configurés au maximum et aucun URL fourni par le
  modèle ; redirection limitée au même hôte.
- Un téléchargement par flux et par appel, timeout configuré à huit secondes
  dans la CLI de recherche ; erreurs isolées par flux et cache HTTP commun.
- Réponse XML plafonnée à 256 Ko, 40 entrées analysées par flux, 20 articles
  retournés par recherche et champs de texte bornés.
- `DOCTYPE` et `ENTITY` sont refusés ; les liens d'articles ne sont jamais
  suivis. Les extraits sont du texte simple et restent explicitement non fiables.
- La fenêtre de publication évaluée est de 30 jours par défaut et se règle avec
  `research --news-window-days` (1–365). Le rapport distingue fenêtre, dates de
  publication, dates de collecte, fraîcheur, pertinence et sources manquantes.
- `get_market_news` reste du contexte macro/réglementaire général, sans
  attribution à l'actif candidat. `get_asset_news` exige une identité vérifiée;
  une correspondance de nom reste possible et explicitement non confirmée.
- Ollama reçoit un index compact (référence, titre borné, éditeur, date,
  pertinence et fraîcheur), avec extrait limité. Les éléments complets restent
  dans le registre du rapport et les références omises sont tracées.
- Les erreurs ou l'absence de date restent visibles comme données manquantes.
  Aucun résultat pertinent ne prouve pas l'absence d'événement, et une collecte
  récente ne rend pas récent un article ancien. Une couverture de source
  partielle ou un arrêt avant conclusion impose un statut partiel par le code.
  Aucun score de sentiment ni classification de stratégie n'est modifié.
