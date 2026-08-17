# Fork perso d'OpenShorts

Ce dépôt est un fork de [mutonby/openshorts](https://github.com/mutonby/openshorts).
J'y ajoute ce dont j'ai besoin et que je ne compte pas forcément remonter en amont :
ingest de fichiers locaux par chemin, whisper large-v3 sur GPU, bibliothèque de
projets, reprise d'un job interrompu.

## Les deux remotes

| Remote | Dépôt | Rôle |
| --- | --- | --- |
| `origin` | `mutonby/openshorts` | l'amont, lecture seule |
| `fork` | `JulienCr/openshorts` | mon fork, là où je pousse |

C'est l'inverse de la convention la plus répandue, où `origin` désigne son propre
dépôt. Ne pas la « corriger » : les alias ci-dessous s'appuient dessus.

## Ce que contient `main`

`main` n'est pas une copie de l'amont. Il porte l'amont **plus** mon travail.
Depuis le merge `1464184` du 16 août 2026, c'est le seul endroit où ce fork existe
en entier, et c'est ce que je déploie.

L'amont arrive par merge, jamais par rebase. Un rebase imposerait un force-push et
me ferait résoudre les mêmes conflits à chaque synchro. Un merge les résout une fois.

## Au quotidien

```bash
git upstream-log     # ce qui est arrivé chez mutonby depuis la dernière synchro
git sync-upstream    # fetch amont, merge dans main, push sur fork
```

`sync-upstream` refuse de tourner ailleurs que sur `main`, pour ne pas rapatrier
l'amont dans une branche de feature par accident. Si le merge conflitte, il s'arrête
avant le push.

En pratique je ne lance pas ces commandes moi-même : je demande à Claude de récupérer
l'amont, et il fait le merge **et** la résolution des conflits. Pas de routine
planifiée, rien ne bouge sur `main` sans que je l'aie demandé.

Les branches de feature partent de `main` et y reviennent par merge, comme dans
n'importe quel dépôt.

## Réinstaller les alias après un clone

Ils vivent dans `.git/config`, que git ne versionne pas. Sur une nouvelle machine :

```bash
git remote add fork https://github.com/JulienCr/openshorts.git

git config --local alias.upstream-log '!git fetch -q origin --prune; git log --oneline --no-merges main..origin/main'

git config --local alias.sync-upstream '!set -e; git fetch origin --prune; b=$(git symbolic-ref --quiet --short HEAD || echo DETACHED); if [ "$b" != main ]; then echo "sync-upstream: place-toi sur main (tu es sur $b)"; exit 1; fi; git merge --no-edit origin/main; git push fork main; echo; echo "main -> $(git log --oneline -1 main)"'
```

Le choix de l'alias plutôt qu'un script versionné tient à ce que tout fichier ajouté
ici devra être porté à chaque merge amont. Ce README échappe à la règle parce que
l'amont n'a aucun fichier de ce nom, donc aucun conflit possible.

## Où les conflits tomberont

Un merge amont ne peut casser que sur les fichiers que le fork a touchés **et
que l'amont connaît**. Ceux que lui seul possède (`README_FORK.md`, les deux
`docker-compose.*.yml`, `local_stage.py`, `scripts/`, `BatchStrip.jsx`, leurs
tests) ne peuvent pas conflicter : ils sont hors du tableau.

Pour les autres, le risque ne se lit pas sur la taille du patch seule. Il faut
la croiser avec ce que l'amont remue au même endroit.

| Fichier | Écart du fork | Commits amont sur 90 j | Ce que le fork y a mis |
| --- | --- | --- | --- |
| `app.py` | +408 | 37 | ingest par chemin, bibliothèque de projets, reprise de job |
| `main.py` | +179 | 42 | reprise de job, sélection des clips en deux passes |
| `clip_selection.py` | +288 | 2 | shortlist proportionnelle, fusion des fenêtres, snapping |
| `dashboard/src/App.jsx` | +24 | 26 | câblage de l'ingest local et de la bibliothèque |
| `dashboard/src/components/MediaInput.jsx` | +152 | 6 | choix d'un fichier serveur, relecture du dossier |
| `CLAUDE.md` | +133 | 8 | tout ce que le fork a ajouté au pipeline |
| `gemini_worker.py` | +35 | 8 | prompts de scoring et de détail |
| `Dockerfile` | +12 | 8 | uid du conteneur en build arg |
| `dashboard/src/components/HistoryTab.jsx` | +12 | 3 | bibliothèque de projets |

`app.py` et `main.py` concentrent le danger : gros patch **et** fichier remué en
amont. `MediaInput.jsx` porte le quatrième plus gros patch du fork mais l'amont
n'y touche presque pas, donc il passe sans bruit ; `clip_selection.py` est le
cas confortable, un très gros écart dans un fichier que l'amont ignore
quasiment (2 commits sur 90 jours) — c'est d'ailleurs la raison de l'y mettre
plutôt que dans `main.py`, en plus de la CI qui ne peut pas importer `main`.

Régénérer la carte après chaque synchro :

```bash
git diff --stat origin/main...main
git log --format='%s' origin/main..main -- <fichier>   # quel commit du fork défend cette zone
git log --oneline --since=90.days origin/main -- <fichier>
```

## Rien ne repart vers l'amont

Pas de commit, pas de PR, pas d'issue sur `mutonby/openshorts`. Le fork ne fait que
recevoir.

Le piège tient au nom : ici `origin` désigne l'amont, alors que la plupart des outils
le traitent comme « mon dépôt » par défaut. `main` suit `fork/main` depuis le
16 août 2026, donc un `git push` nu tombe au bon endroit, mais `gh` continue de viser
`origin` tant qu'on ne le lui interdit pas.

```bash
gh issue create -R JulienCr/openshorts   # sans -R, l'issue part chez mutonby
git push fork <branche>                  # viser le remote plutôt que se fier au défaut
```

## Branches qui ne sont pas de moi

GitHub recopie toutes les branches du projet original au moment du fork.
`feat/clip-editor` (victorcavero14), `feat/paid-mode` (juancarlos.cavero) et
`gpu-migration` sont arrivées comme ça. Elles ont été supprimées du fork le
16 août 2026 : elles pointaient sur le même commit que celles de l'amont, qui les
garde. Le fork ne porte donc plus que `main`.

Avant d'intégrer une branche, regarder qui l'a écrite :

```bash
git log --format='%an' main..origin/<branche> | sort -u
```
