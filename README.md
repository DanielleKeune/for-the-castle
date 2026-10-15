# IA_final : ce qui a changé par rapport à IA_Base

Ce document compare l'IA de départ ([IA_Base](../IA_Base/)) et l'IA finale ([IA_final](./)) pour le jeu « Battez-vous ! ».
Les deux IA ont la même structure de fichiers :

| Fichier | Rôle |
|---|---|
| `economy.py` | Pions, récolte, recrutement, réparation |
| `combat.py` | Déplacements et attaques des combattants |
| `template.py` | Client réseau (identique dans les deux) |

Seule la stratégie change.

---

## 1. Résultats

80 parties par adversaire : 40 cartes (graines 1 à 40), chacune jouée côté violet puis côté jaune. Contre le robot, IA_Base n'a joué que le côté violet (40 parties).
Format : **victoires / défaites / nuls**.

| Adversaire | IA_Base | IA_final |
|---|---|---|
| Robot fourni | 0 / 40 / 0 | **80 / 0 / 0** |
| IA2 | 0 / 80 / 0 | **80 / 0 / 0** |
| IA3 | 13 / 67 / 0 | **74 / 4 / 2** |
| IA4 v1 (attaque précoce) | 0 / 80 / 0 | **64 / 16 / 0** |
| IA4 v2 (défense puis contre-attaque) | 0 / 80 / 0 | **70 / 7 / 3** |
| **IA_Base contre IA_final** | 0 / 80 / 0 | **80 / 0 / 0** (fin moyenne au tour 94) |

IA_Base perd la plupart de ses parties avant le tour 110 (un peu plus tard contre IA3). IA_final gagne contre tous les adversaires testés.

---

## 2. Les différences en un coup d'œil

| Thème | IA_Base | IA_final |
|---|---|---|
| **Pions** | 5, puis 8 (6 viande / 2 or), 9 s'il faut du bois | **14**, puis **12** après le tour 300 |
| **Répartition or / viande** | Quotas fixes, stock d'or régulé entre 30 et 32 | Calculée sur les **5 prochains recrutements** et la distance des gisements, avec 1 pion de changement par tour au maximum |
| **Armée** | 2 guerriers pour 1 lancier, 1 moine à 12 combattants, **aucun archer** | **16 lanciers, 8 archers** (10 en fin de partie), **2 moines**, **jamais de guerrier** |
| **Défense** | 1 à 3 gardes, le reste attaque en continu | **Toute l'armée défend** tant que l'assaut n'est pas décidé |
| **Déclenchement de l'attaque** | Permanent : chaque recrue part dès qu'elle existe | **Rapport de forces** (loi de Lanchester), vague groupée |
| **Assaut** | Unités isolées qui arrivent une par une | **Tout le monde part ensemble** (lanciers, archers, moines) ; seuls les pions restent |
| **Tirs** | Cible à portée | **Tir concentré** : dégâts prévus, priorité aux archers et moines, achever ce qui peut l'être |
| **Archers sous pression** | (pas d'archers) | **Reculent d'une case** quand un ennemi arrive à 2 cases, en restant à portée de tir |
| **Réparation** | Dès qu'il y a du bois (gaspillage) | Sans gaspillage, **réserve de 100 bois** pour le dernier tour |
| **Fin de partie** | Rien de particulier | **Phase finale complète** au tour 500 : archers de contournement, lanciers, pions frappeurs, 5 gardes avec du bois |

---

## 3. Le plan de jeu, phase par phase

### IA_Base : attaque continue

```
 tour 0 ──────── ~40 ──────────────── ~100 ──────────────────────── 500
   │              │                     │
   │  5 pions     │  guerriers +        │  (château détruit en moyenne
   │  puis        │  lanciers envoyés   │   vers le tour 100 contre
   │  soldats     │  un par un vers     │   les autres IA)
   │              │  le château adverse │
   ▼              ▼                     ▼
 [ÉCO MINIMUM]  [FILET CONTINU DE SOLDATS] ──► [DÉFAITE]
```

Le problème de fond : chaque soldat arrive **seul** devant une défense groupée et meurt sans résultat. Avec seulement 8 pions, l'économie ne suit pas quand le coût en viande augmente (le n-ième recrutement coûte n viandes).

### IA_final : économie, défense groupée, assaut décidé, fin de partie préparée

```
 tour 0 ──── ~40 ──── ~70 ─────────────── ~150-300 ──────── 300 ─────── 400 ── 470 ── 494 ── 500
   │          │         │                     │               │           │      │      │      │
   │ 14 pions │ archers │  lanciers ; toute   │  ASSAUT si    │ 2 pions   │ les  │ 5    │ tout │ dépôt
   │ (viande  │ et      │  l'armée défend le  │  le rapport   │ sacrifiés │ lan- │ gar- │ frap-│ du bois
   │ bon      │ lanciers│  château            │  de forces    │ → 2       │ ciers│ des  │ pe   │ puis
   │ marché)  │ alternés│                     │  est favorable│ archers de│ par- │ +    │ le   │ grande
   │          │ moines  │                     │  (repli sinon)│ contourne-│ tent │ bois │ châ- │ répa-
   │          │ au 25e  │                     │               │ ment      │      │      │ teau │ ration
   ▼          ▼         ▼                     ▼               ▼           ▼      ▼      ▼      ▼
 [ÉCONOMIE] [GARDE] [ARMÉE QUI GROSSIT] [ASSAUTS GROUPÉS] [PRÉPARATION]  [PHASE FINALE]  [DÉPARTAGE]
```

| Phase | Tours | Objectif |
|---|---|---|
| 1. Économie | 0 → ~40 | 14 pions tant que la viande ne coûte presque rien |
| 2. Garde | ~40 → ~70 | 8 archers en alternance avec les premiers lanciers |
| 3. Armée | ~70 → … | 16 lanciers et 2 moines ; tout le monde reste groupé au château |
| 4. Assauts | dès que favorable | Toute l'armée part ; repli si le rapport de forces s'inverse |
| 5. Préparation | 300 → 470 | 2 archers de contournement ; réserve de bois |
| 6. Phase finale | 400 → 500 | Tout ce qui peut frapper le château adverse arrive vers le tour 494-498 |
| 7. Départage | 500 | Réparation maximale ; le château qui a le plus de PV gagne |

---

## 4. Économie

### Nombre de pions

```
IA_Base   ████████ 8 pions (+1 pour le bois)
IA_final  ██████████████ 14 pions  →  ████████████ 12 après le tour 300
```

Le coût en viande d'un recrutement est égal à son rang : le 3e coûte 3, le 30e coûte 30. Les pions recrutés en premier coûtent donc très peu et rapportent pendant toute la partie. IA_final en prend 14 avant de lancer l'armée.

### Répartition or / viande

```
IA_Base : quotas fixes
    ┌───────────────────────────────┐
    │ 6 pions viande │ 2 pions or   │   stock d'or > 32 → les pions d'or passent à la viande
    └───────────────────────────────┘   stock d'or < 30 → ils reviennent à l'or

IA_final : quotas calculés à chaque tour
    besoins des 5 prochains recrutements (or + viande)
            │
            ▼
    charge = (besoin + manque par rapport au stock) / rendement d'un pion
             (le rendement dépend de la distance du gisement)
            │
            ▼
    part de l'or = charge or / (charge or + charge viande)
            │
            ▼
    au plus 1 pion de changement par tour (pas de demi-tours inutiles)
```

Deux garde-fous ont été ajoutés après les tests :

- **Le calcul suit l'unité réellement voulue.** Si le château est menacé, le prochain recrutement est un lancier (10 or) et non un pion (1 or). Sans ce correctif, l'IA s'est déjà retrouvée bloquée avec 1722 viandes et 9 or pendant 450 tours.
- **Si la viande est couverte et que l'or bloque**, au moins la moitié des pions vont sur l'or.

### Bois et réparation

| | IA_Base | IA_final |
|---|---|---|
| Quand récolter du bois | Château abîmé de 80 PV ou plus | Dès 30 PV perdus (2 bûcherons à partir de 150), **et** réserve de 100 bois à partir du tour 400 |
| Quand réparer | Dès qu'il y a du bois | Seulement si le bois ne dépasse pas les dégâts (la réparation consomme tout le stock), sauf si le château est sous la moitié de ses PV |
| Dernier tour | Rien de prévu | **Réparation forcée au tour 500**, après le dépôt du bois des 5 gardes |

Le moteur n'accepte qu'**une réparation par tour**, et elle **consomme tout le bois du stock**. IA_final garde donc son bois et fait une seule grande réparation au tour 500. Elle a lieu après les dégâts de ce tour, et c'est elle qui compte pour le départage.

---

## 5. Composition de l'armée

```
                 IA_Base (proportions)                    IA_final (armée complète, 40 unités max)
 Pions           ████████        8 (9 pour le bois)        ██████████████          14 (puis 12)
 Guerriers       ██████          2 guerriers ...           —                        0
 Lanciers        ███             ... pour 1 lancier        ████████████████        16
 Archers         —               0                         ████████                 8 (puis 10)
 Moines          █               1 au plus                 ██                       2
```

Pourquoi ces choix :

- **Le lancier plutôt que le guerrier.** À budget égal, le lancier (90 PV, 10 de dégâts, portée 2) bat le guerrier (60 PV, 8 de dégâts) dès le début de partie. L'écart grandit avec le coût de la viande, puisque chaque recrutement coûte de plus en plus cher. Faute d'or, IA_final attend plutôt que d'acheter un guerrier.
- **Les archers (portée 4).** Ils tiennent le château et accompagnent l'assaut. Ils sont fragiles (35 PV), d'où la règle de recul.
- **Les moines (soin de 10).** 2 au maximum, à partir du 25e recrutement. Ils soignent la garde au repos et suivent la vague pendant l'assaut.

Ordre de recrutement d'IA_final ([economy.py:1656](economy.py#L1656)) :

```
château menacé ? ──oui──► lancier
       │                 (ou un pion s'il en manque et que l'or ne suffit pas pour un lancier)
       non
       ▼
moins de 14 pions (12 après le tour 300) ? ──oui──► pion
       ▼
il faut du bois ? ──oui──► 1 pion de plus
       ▼
moins de 8 archers (10 après le tour 300) ? ──oui──► archer
       │                                             (au plus 2 archers d'avance sur les lanciers
       │                                              tant qu'il y a moins de 6 lanciers)
       ▼
25e recrutement ou plus, et moins de 2 moines ? ──oui──► moine
       ▼
moins de 16 lanciers ? ──oui──► lancier, sinon rien
```

---

## 6. Combat

### Défense : la garde du château

```
IA_Base : 1 à 3 gardes, le reste part      IA_final : toute l'armée en couronne

        . . . . . . . . .                      . . L . . L . . L . .
        . . . . . . . . .                      . L . A . A . A . L .
        . . G . . . G . .                      . . A . M . M . A . .
        . . . ┌───────┐ .   ──► soldats         L . . ┌───────┐ . . L
        . . . │CHÂTEAU│ .       isolés          . . A │CHÂTEAU│ A . .
        . . . └───────┘ .       vers l'ennemi   L . . └───────┘ . . L
        . . . . G . . . .                      . . A . . . . . A . .
                                                . L . . L . . L . . .

   G = garde                                L = lancier (couronne à 2-5 cases)
                                            A = archer (zone à 2-4 cases, près d'un moine)
                                            M = moine (5 cases du château au plus)
```

### Le déclenchement de l'assaut

IA_final compare la **force** des deux armées selon la loi de Lanchester : force = somme des PV × somme des dégâts.

```
                          ┌────────────────────────────────────────────┐
                          │                  DÉFENSE                   │
                          │  toute l'armée groupée autour du château   │
                          └──────────────────────┬─────────────────────┘
                                                 │ au moins 6 combattants de campagne
                                                 │ ET aucun ennemi à 6 cases du château
                                                 │ ET (notre force ≥ 1,2 × la leur
                                                 │     OU 16 lanciers et force ≥ 1,3 × la leur)
                                                 │ ET, après le tour 300 contre une armée
                                                 │     au complet restée chez elle :
                                                 │     nos PV ≥ 1,3 × les siens
                                                 ▼
                          ┌────────────────────────────────────────────┐
                          │                   ASSAUT                   │
                          │  lanciers + archers + moines partent       │
                          │  ensemble ; seuls les pions restent        │
                          └──────────────────────┬─────────────────────┘
                                                 │ 2 combattants ou moins
                                                 │ OU notre force < celle de l'adversaire
                                                 ▼
                                    retour en DÉFENSE (repli)
```

Pendant l'assaut :

- **Les archers marchent en tête.** Les lanciers ne les distancent pas de plus de 3 cases, sauf pour engager un ennemi à 6 cases ou moins.
- **Les archers visent d'abord l'armée adverse**, puis le château quand elle a reculé.
- **Les moines suivent la vague** et soignent ses combattants.

### Le tir concentré

```
pour chaque attaquant, parmi les cibles à portée :
   1. ignorer une cible déjà condamnée par les tirs prévus ce tour (dégâts gaspillés)
   2. priorité : archer / moine  >  guerrier / lancier  >  pion
   3. préférer une cible que ce tir peut achever
   4. puis la cible la plus abîmée
```

### Archers en retrait

```
 avant :   L→ A          (le lancier ennemi L arrive au contact de l'archer A : l'archer meurt)
 après :   L→ . A        (l'archer recule d'une case, reste à portée 4 et continue de tirer)
```

Ce seul changement fait passer IA_final de 30 à 16 défaites contre IA4 v1 ([combat.py:927](combat.py#L927)).

---

## 7. Fin de partie : préparer le départage du tour 500

Au tour 500, la partie se termine et **le château qui a le plus de PV gagne**. IA_Base n'en tient pas compte. IA_final prépare ce moment à partir du tour 300.

### Chronologie

```
 300        2 pions vides sont sacrifiés : ils marchent vers le château adverse, ce qui
            libère 2 places sous le plafond de 40 unités ; 2 archers de contournement
            sont recrutés.
 300-480    les 2 archers contournent la carte, chacun par un côté, et attendent à
            10-11 cases du château adverse, hors de vue de sa défense.
 400        réserve de bois : 100 bois en stock.
 400-494    chaque lancier part au dernier moment, selon son trajet et sa vitesse,
            pour arriver à portée du château adverse au tour 494.
 470        les 5 pions les plus proches du château deviennent des gardes : chacun
            fait un plein de bois (5) et attend contre le château. Les autres pions
            deviennent des frappeurs.
 ~490       les frappeurs foncent (4 cases par tour) pour arriver au contact du
            château adverse au tour 498.
 494-498    les archers de contournement se mettent à portée au tour 498.
 494-500    lanciers, archers de contournement et frappeurs ne tapent plus QUE le
            château adverse : les pertes n'ont plus d'importance.
 500        les 5 gardes déposent leur bois, puis grande réparation (après les
            dégâts du tour) ; départage aux PV.
```

### Sur la carte

```
          ┌──── archer de contournement n°1 (côté gauche) ─────┐
          │                                                    ▼
 ┌──────────────┐                                         attente à 10-11 cases
 │ NOTRE CHÂTEAU│  lanciers ──────────────────────────►   ┌──────────────────┐
 │  5 gardes    │  pions frappeurs ═══════════════════►   │ CHÂTEAU ADVERSE  │
 │  + 125 bois  │                                         └──────────────────┘
 │  archers     │                                         attente à 10-11 cases
 │  moines      │                                                    ▲
 └──────────────┘                                                    │
          └──── archer de contournement n°2 (côté droit) ──────┘
```

Les archers et les moines de garde restent à la maison : ils sont lents, et une riposte tardive trouverait le château vide. Envoyer aussi les archers et les moines a été testé : 72 victoires contre 70 face à IA4 v2, mais un peu moins bien contre les autres adversaires. Ce n'est donc pas un avantage net.

### Ce que ça change

| Contre IA4 v2 | Victoires / Défaites / Nuls |
|---|---|
| Sans aucune préparation de fin de partie | 10 / 12 / 18 (sur 40 parties) |
| Avec les 2 archers de contournement | 16 / 18 / 6 (sur 40 parties) |
| Avec en plus les gardes, le bois et les pions frappeurs | 44 / 33 / 3 (sur 80 parties, après d'autres réglages) |
| **IA_final, avec en plus la frappe des lanciers** | **70 / 7 / 3** (sur 80 parties) |

Les deux premières lignes portent sur 40 parties, les deux suivantes sur 80, et d'autres réglages ont changé entre-temps. Comparez surtout le nombre de nuls : ce sont eux que la fin de partie transforme en victoires.

---

## 8. Comment on est arrivé là

Chaque modification a été validée par des batteries de parties automatiques contre toutes les autres IA. Celles qui faisaient perdre ont été abandonnées.

| Étape | Modification | Effet principal |
|---|---|---|
| IA2 | Regroupement, tir concentré, moines au château | Ne perd plus contre IA_Base |
| IA3 | 14 pions, 4 archers de garde, lanciers plutôt que guerriers, vague de 20 | Bat le robot 37 fois sur 40 |
| IA3v2 | 12-14 pions, 8 archers, 16 lanciers, assaut selon le rapport de forces | Bat IA3 |
| + | Jamais de guerrier, repli tant que le rapport de forces est défavorable, réparation sans gaspillage | Plus régulier |
| + | 2 archers de contournement, sacrifice de 2 pions | Les nuls deviennent des victoires |
| + | 5 gardes avec du bois, pions frappeurs, attente à 10-11 cases | Contre IA4 v2 : 44 victoires au lieu de 34 |
| + | Assaut avec tout le monde, départ à 1,3, correctif de l'or | Contre IA4 v2 : 16 défaites au lieu de 33 |
| + | Archers en retrait | Contre IA4 v1 : 16 défaites au lieu de 30 |
| + | Prudence ciblée au grand choc des tours 300+, pions si l'or manque | Contre IA4 v2 : 7 défaites ; robot : 80 sur 80 |
| **IA_final** | Phase finale des lanciers | Contre IA4 v2 : 70 / 7 / 3 |

Pistes testées puis abandonnées :

| Piste | Résultat |
|---|---|
| 16 pions | Moins bon contre IA4 v1 |
| 3 lanciers précoces | 54 défaites contre IA4 v1 |
| Pions qui fuient plus tôt | Trop de récolte perdue |
| Rapport de forces en PV appliqué à tous les assauts | Trop prudent : −16 victoires contre IA3v2_d |
| Attente des archers à 8-9 cases | Repérés et tués |

---

## 9. Réglages principaux d'IA_final

| Réglage | Valeur | Fichier |
|---|---|---|
| `PAWN_TARGET` | 14 (12 après le tour 300) | [economy.py:11](economy.py#L11) |
| `ARCHER_GUARDS` | 8 (10 après le tour 300) | [economy.py:14](economy.py#L14) |
| `LANCER_TARGET` | 16 | [economy.py:17](economy.py#L17) |
| `MAX_MONKS` / `MONK_FROM_RECRUIT` | 2 / à partir du 25e recrutement | [economy.py:7](economy.py#L7) |
| `ARCHER_LEAD` | 2 archers d'avance au plus sur les lanciers | [economy.py:21](economy.py#L21) |
| `FINAL_PHASE_TURN` | 470 (gardes et frappeurs) | [economy.py:35](economy.py#L35) |
| `WOOD_RESERVE` | 100 bois | [economy.py:41](economy.py#L41) |
| `FLANK_START` | 300 | [economy.py:48](economy.py#L48) |
| `WAVE_SIZE` | 16 lanciers = armée finale | [combat.py:10](combat.py#L10) |
| `ATTACK_RATIO` / `FULL_ARMY_RATIO` | 1,2 / 1,3 | [combat.py:14](combat.py#L14) |
| `LATE_CLASH_TURN` | 300 (prudence au grand choc) | [combat.py:19](combat.py#L19) |
| `LEASH` | 3 cases d'avance au plus pour les lanciers | [combat.py:39](combat.py#L39) |
| `FLANK_STAGING` | attente à 10-11 cases | [combat.py:47](combat.py#L47) |
| `FINAL_START` / `FINAL_ARRIVAL` | les lanciers partent dès le tour 400, arrivée au tour 494 | [combat.py:646](combat.py#L646) |

Les tours de fin de partie (300, 400, 470, 494, 498) sont donnés pour une partie de 500 tours. Si le moteur annonce un autre `max_turns`, ils sont décalés pour garder la même distance avec le dernier tour (fonction `_turn`). Par exemple, dans une partie de 300 tours, le contournement démarre au tour 100 et les gardes au tour 270.

---

## 10. Lancer IA_final

Depuis le dossier `tactical` :

```
python IA_final/template.py <ID_PARTIE> --name IA_final
```

`<ID_PARTIE>` est l'identifiant affiché dans la fenêtre du jeu.

IA_final a été vérifiée sur le vrai jeu (dossier `vrai_jeu` : moteur Godot `cours-isen.exe` et `server.py`). Elle a gagné ses 9 parties contre le vrai robot (`robot.exe`), des deux côtés, entre les tours 97 et 214.

Le vrai moteur envoie les mêmes règles que notre copie, à un détail près : le coût des unités est donné sous la forme `{"gold": 10}` au lieu de `10`. IA_final lit les deux formats.

La connexion n'impose plus de délai une fois établie, ce qui évite une déconnexion si le moteur met plus de 10 s à envoyer un tour.
