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
avant le push et laisse la résolution à la main.

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
