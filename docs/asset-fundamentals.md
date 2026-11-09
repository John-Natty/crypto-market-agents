# Dossiers d'identité et de fondamentaux

La commande `crypto-market-agents enrich --analysis-json ... --json ...`
produit un dossier JSON distinct à partir des candidats du rapport d'analyse.
Elle est en lecture seule, n'appelle aucun modèle et ne modifie ni rapport ni
snapshot source. Le chemin de sortie doit être nouveau : un fichier existant
est refusé.

## Identité et associations

Le symbole Bitvavo sert uniquement à découvrir des candidats CoinGecko. Il
n'est jamais une preuve d'identité, même si un seul résultat semble
correspondre. Les mappings vérifiés restent prioritaires et ne sont jamais
écrits ou modifiés automatiquement par l'enrichissement.

Le nom et les réseaux pris en charge viennent de l'endpoint public Bitvavo
`GET /assets`, associé au symbole de base déjà présent dans le rapport Bitvavo.
Cet endpoint ne fournit pas d'adresse de contrat. Quand réseau et contrat sont
disponibles côté CoinGecko, ils sont conservés comme observations de cette
source; Bitvavo n'est pas présenté comme leur source.

En l'absence de mapping, le code peut vérifier une paire exacte via CoinGecko
`GET /coins/{id}/tickers?exchange_ids=bitvavo`. Une seule entrée fraîche et
non anormale dont `base` et `target` correspondent exactement au marché
Bitvavo du rapport peut relier l'ID CoinGecko à ce marché. La preuve conserve
la source CoinGecko, la dernière transaction, la paire, l'exchange et la date
de collecte Bitvavo du marché. Si plusieurs IDs ont la même paire, si la
preuve est périmée ou si le champ source est absent, l'identité reste
`unresolved`. Cette résolution est calculée à la volée dans le dossier; elle
n'ajoute rien au fichier de mappings. Le modèle ne peut ni proposer ni
enregistrer lui-même une correspondance.

Le fichier facultatif `--mapping` est un objet JSON indexé par marché Bitvavo
(recommandé) ou symbole. Exemple de structure :

```json
{
  "ABC-EUR": {
    "coingecko_id": "provider-id",
    "network": "ethereum",
    "contract_address": "0x...",
    "defillama_slug": "protocol-slug"
  }
}
```

L'ID doit exister dans `/coins/list` et son symbole doit correspondre à celui
du marché. Si réseau et contrat sont fournis, ils doivent correspondre à la
plateforme/contrat renvoyés par CoinGecko. Les correspondances doivent être
vérifiées et maintenues par l'opérateur; une valeur de mapping n'est pas
validée par Bitvavo si le marché source ne contient pas lui-même cette
métadonnée. Les alias réseau comparés sont limités à ETH/Ethereum, BASE/Base
et TON/The Open Network.

Le dépôt fournit `data/asset-identity-mappings.json`, fondé sur la collecte
Bitvavo/CoinGecko du 9 novembre 2026. RSR, O, HMSTR et PEPE ont une entrée car
nom exact, symbole et réseau Bitvavo correspondent chacun à un ID CoinGecko
et à son contrat sur la plateforme correspondante. PEPE a plusieurs candidats
au seul symbole; le mapping explicite choisit l'entrée au nom exact Pepe et au
réseau Ethereum. ICNT reste non résolu : le nom CoinGecko contient un suffixe
différent et Bitvavo ne fournit pas l'adresse de contrat pour lever
l'incertitude. Les adresses inscrites proviennent de CoinGecko et ne sont pas
présentées comme des adresses publiées par Bitvavo.

Un token n'est associé à un protocole DefiLlama que si le mapping explicite
fournit son slug et que ce slug existe exactement dans `/protocols`. Le slug
doit avoir été vérifié par l'opérateur; le nom et le symbole seuls ne sont
jamais utilisés. La TVL reste une mesure des
actifs verrouillés dans un protocole; elle ne représente ni capitalisation du
token, ni revenus, ni trésorerie.

## Provenance, valeurs absentes et limites

Chaque observation indique source, date de collecte, date source si fournie,
unité, disponibilité et raison d'absence. Les nombres absents sont `null`,
jamais zéro. Les descriptions externes sont marquées
`untrusted_external_data` et doivent être traitées comme des données non
fiables, jamais comme instructions.

Les endpoints initiaux sont publics et sans clé obligatoire : CoinGecko
`GET /api/v3/coins/list?include_platform=true` et
`GET /api/v3/coins/{id}`; DefiLlama Free API `GET /protocols` puis
`GET /protocol/{slug}` pour le protocole apparié. Les réponses passent par le
cache HTTP partagé (TTL configurable via `HTTP_CACHE_TTL_SECONDS`, cache
activé par défaut) et la politique commune de retries. Les limites publiques
CoinGecko peuvent varier et provoquer des réponses 429; le client applique
les retries communs, sans basculer vers une API payante. Le dossier espace
les appels CoinGecko d'au moins 12 secondes (au plus cinq appels/minute,
plus conservateur que le quota public indiqué de 5 à 15 appels/minute).
Aucun abonnement,
essai ou secret n'est requis. Une erreur de source est inscrite dans chaque
dossier concerné sans supprimer les autres candidats.

Le chemin actuel ne collecte pas toutes les chaînes de contrats possibles,
les revenus, la trésorerie, les déblocages ou la concentration des détenteurs.
La disponibilité et l'exactitude des champs dépendent des fournisseurs; le
dossier n'est ni une évaluation ni une recommandation d'achat.

## Contrats API officiels

- [CoinGecko coins list](https://docs.coingecko.com/reference/coins-list)
- [CoinGecko coin data by ID](https://docs.coingecko.com/reference/coins-id)
- [CoinGecko coin tickers by ID](https://coingecko-api-v3.readme.io/reference/coins-id-tickers)
- [CoinGecko markets endpoint and ID filtering](https://docs.coingecko.com/reference/coins-markets)
- [Bitvavo public asset metadata endpoint](https://docs.bitvavo.com/docs/rest-api/get-asset-data/)
- [CoinGecko public API rate limits](https://support.coingecko.com/articles/5125303456-what-is-the-rate-limit-for-coingecko-api-public-plan)
- [DefiLlama API documentation](https://api-docs.defillama.com/)
- [DefiLlama data definitions](https://docs.llama.fi/analysts/data-definitions)
- [CoinGecko public API rate limits](https://support.coingecko.com/articles/5125303456-what-is-the-rate-limit-for-coingecko-api-public-plan)
