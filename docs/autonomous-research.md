# Recherche autonome en lecture seule

La commande `research` lance une boucle de recherche locale et bornée. Le
scanner fournit une présélection, puis Ollama choisit à chaque tour un seul
outil autorisé en fonction de l'objectif et des observations déjà reçues. Le
programme n'impose donc pas une collecte historique et fondamentale pour
chaque candidat.

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

## Outils que le modèle peut demander

- `analyze_candidate`: analyse historique d'un marché de la présélection.
- `get_fundamentals`: dossier factuel d'identité, CoinGecko et éventuelle
  correspondance DefiLlama vérifiée.
- `finish_research`: choisit les références de faits et d'informations
  manquantes à transmettre à la phase de conclusion sans outil.

Les arguments sont validés par le programme. Les mappings explicites vérifiés
restent prioritaires et ne sont jamais modifiés par la recherche. Si aucun
mapping ne s'applique, le dossier peut recouper les candidats CoinGecko avec
le marché exact Bitvavo annoncé par le scanner : une seule paire CoinGecko
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
Le budget total comprend scan, attente de service, appels d'outil et tours
Ollama. Les réponses externes et les appels de génération sont plafonnés.
Un appel identique n'est pas répété sans changement de données ou reprise
autorisée par le code. Un budget épuisé ou une erreur produit un rapport
partiel avec la raison, les appels, les erreurs et les résultats déjà
conservés. Chaque requête Ollama est reconstruite depuis un registre à
références stables; les diagnostics et réponses brutes des outils ne sont pas
réinjectés dans les tours suivants. Les mesures principales du scanner sont
transmises une seule fois dans la présélection compacte, avec leurs références
`scan:<marché>:<métrique>`; les diagnostics détaillés restent dans le rapport.
Cette réduction est indiquée dans la politique de contexte envoyée au modèle.
Un estimateur calibré sur les comptes de
prompt Ollama observés, `ceil(JSON UTF-8 bytes / 4.5)`, budgète instructions,
schémas d'outils et
observations, puis ajoute les tokens de génération demandés et une marge de
sécurité. Si nécessaire, le scanner retire de la présélection les candidats
les moins prioritaires qui n'ont pas encore été approfondis; chaque retrait
est tracé. Les valeurs textuelles abrégées sont signalées par référence et
restent complètes dans le rapport. Si le contexte ne tient toujours pas, la
recherche s'arrête au lieu de tronquer silencieusement des preuves.

Le rapport v3 distingue tokens réservés et tokens effectivement générés par
phase. Chaque tour conserve `think=false`, le compte d'évaluation du prompt,
les tokens générés, les octets, la durée, les outils demandés et les répétitions
détectées, l'estimation de prompt, la marge et les références abrégées. Si le JSON final échoue, le rapport produit un résumé déterministe
des outils, preuves, manques et erreurs, sans fabriquer de classification.

Le JSON conserve les requêtes d'outil structurées, leur statut, les sources,
les horodatages, les références disponibles/manquantes et les compteurs de
budget. Il ne conserve pas le raisonnement interne détaillé. Les descriptions
et autres contenus externes sont des données non fiables et ne sont jamais
traités comme des instructions.

Cette fonctionnalité n'exécute que les outils de lecture listés ci-dessus.
Elle ne passe aucun ordre, ne modifie pas de portefeuille et ne donne aucune
autorisation d'agir sur les prix actuels. Une analyse historique reste
descriptive des fenêtres qu'elle rapporte.
