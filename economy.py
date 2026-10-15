from collections import deque


# STRATEGIE IA3 v2 — composition 14 pions / 8 archers / 2 moines / 16 lanciers
# (= 40 unités, la limite), répartition or/viande selon les besoins.

MONK_FROM_RECRUIT = 25
MAX_MONKS = 2

# Nombre de pions visé avant de produire l'armée (hors menace).
PAWN_TARGET = 14

# Archers gardés en permanence au château (ils restent près des moines).
ARCHER_GUARDS = 8

# Taille maximale de l'armée de campagne (lanciers, et guerriers bouche-trou).
LANCER_TARGET = 16

# Alternance archers / lanciers : au plus ARCHER_LEAD archers d'avance sur les lanciers,
# jusqu'à ce qu'il y ait ARCHER_LEAD_UNTIL lanciers.
ARCHER_LEAD = 2
ARCHER_LEAD_UNTIL = 6

# Réparation du château : bûcherons dès WOOD_FROM_MISSING PV perdus (2 bûcherons à
# partir de WOOD_TWO_WORKERS_FROM), et réserve de WOOD_RESERVE bois à partir du tour
# LATE_GAME_TURN pour le départage aux PV du dernier tour.
WOOD_FROM_MISSING = 30
WOOD_TWO_WORKERS_FROM = 150
# Coup final : à partir de FINAL_PHASE_TURN, FINAL_GUARDS pions restent au château,
# chacun chargé d'un plein de bois, et le déposent au dernier tour juste avant la
# réparation. Les autres pions partent frapper le château adverse pour y arriver au
# tour PAWN_STRIKE_TURN. Le stock garde de quoi faire FINAL_REPAIRS réparations de
# WOOD_PER_REPAIR PV (le serveur n'accepte qu'une réparation par tour et consomme
# tout le bois : la réserve est donc utilisée en une fois au dernier tour).
FINAL_PHASE_TURN = 470
FINAL_GUARDS = 5
GUARD_RETURN_TURNS = 6
PAWN_STRIKE_TURN = 498
FINAL_REPAIRS = 5
WOOD_PER_REPAIR = 20
WOOD_RESERVE = FINAL_REPAIRS * WOOD_PER_REPAIR
_FINAL = {}              # camp -> {"guards": ids des gardes, "launched": frappeurs partis}
LATE_GAME_TURN = 400

# Contournement de fin de partie : à partir de FLANK_START, FLANK_ARCHERS pions sont
# sacrifiés (si l'effectif est plein) pour recruter autant d'archers qui contournent
# la carte et frappent le château adverse aux derniers tours (départage aux PV).
FLANK_START = 300
FLANK_ARCHERS = 2
_SACRIFICE = {}          # camp -> ids des pions sacrifiés


def _turn(rules, turn_at_500):
    """Tour équivalent à `turn_at_500` quand la partie dure rules["max_turns"] tours.

    Les réglages de fin de partie sont écrits pour une partie de 500 tours ; ils
    sont décalés pour garder la même distance avec le dernier tour.
    """
    max_turns = int((rules or {}).get("max_turns", 500) or 500)
    return max(0, turn_at_500 + max_turns - 500)

# ============================================================
# OUTILS DE BASE
# ============================================================

def chebyshev_distance(x1, y1, x2, y2):
    """
    Calcule la distance de Chebyshev.

    Dans le jeu, la récolte et le dépôt utilisent cette distance.
    Les diagonales comptent donc comme 1 case.

    Exemple :
        (10, 10) -> (11, 11) = distance 1
    """
    return max(abs(x1 - x2), abs(y1 - y2))


def _get_gold_cost(rules, unit_type):
    """
    Retourne le coût en or d'un type d'unité.

    Le coût peut être fourni par les règles :
        - directement comme un nombre
        - ou sous forme de dictionnaire contenant "gold"
    """

    unit_rules = rules.get(
        "units",
        {}
    ).get(
        unit_type,
        {}
    )

    cost = unit_rules.get(
        "cost",
        0
    )

    # --------------------------------------------------------
    # Cas 1 : le coût est directement un nombre
    # Exemple : "cost": 5
    # --------------------------------------------------------

    if isinstance(cost, (int, float)):
        return cost

    # --------------------------------------------------------
    # Cas 2 : le coût est un dictionnaire
    # Exemple : "cost": {"gold": 5}
    # --------------------------------------------------------

    if isinstance(cost, dict):

        gold = cost.get(
            "gold",
            0
        )

        if isinstance(gold, (int, float)):
            return gold

    # --------------------------------------------------------
    # Sécurité
    # --------------------------------------------------------

    fallback_costs = {
        "pawn": 1,
        "warrior": 5,
        "lancer": 10,
        "archer": 10,
        "monk": 15,
    }

    return fallback_costs.get(
        unit_type,
        0
    )

def get_my_pawns(state, team):
    """
    Retourne uniquement les pions de notre équipe.
    """
    return [
        unit
        for unit in state.get("units", [])
        if unit.get("team") == team
        and unit.get("type") == "pawn"
    ]


def get_my_castle(state, team):
    """
    Retourne le château de notre équipe.
    """
    for building in state.get("buildings", []):
        if (
            building.get("team") == team
            and building.get("type") == "castle"
        ):
            return building

    return None


def get_my_resources(state, team):
    """
    Retourne les stocks de notre équipe :
        gold = or
        wood = bois
        meat = viande
    """
    return state.get("teams", {}).get(team, {}).get(
        "resources",
        {
            "gold": 0,
            "wood": 0,
            "meat": 0
        }
    )


def _normalize_resource_type(value):
    """Convertit les noms de terrain et de ressources vers gold/wood/meat."""
    if not isinstance(value, str):
        return value
    value = value.lower()
    aliases = {
        "gold": "gold", "mine": "gold", "ore": "gold",
        "wood": "wood", "tree": "wood", "forest": "wood",
        "meat": "meat", "sheep": "meat", "food": "meat",
    }
    return aliases.get(value, value)


def get_resource_positions(state, resource_type=None):
    """Retourne les gisements, en tolérant les noms tree/sheep/wood/meat."""
    resources = state.get("resources", [])
    if resource_type is None:
        return resources
    wanted = _normalize_resource_type(resource_type)
    return [r for r in resources
            if _normalize_resource_type(r.get("resource", r.get("type"))) == wanted]


# ============================================================
# GESTION DE LA CARTE
# ============================================================

def _legend_name(state, rules, x, y):
    """
    Transforme le code numérique de grid[y][x]
    en nom de terrain.

    Exemple :
        1 -> ground
        3 -> gold
        4 -> tree
        5 -> sheep
    """

    grid = state.get("grid", [])

    if not grid:
        return None

    if not (0 <= y < len(grid)):
        return None

    if not (0 <= x < len(grid[y])):
        return None

    legend = rules.get("legend", [])
    code = grid[y][x]

    if not isinstance(code, int):
        return code if isinstance(code, str) else None

    # Certaines versions du moteur donnent une liste, d'autres une table.
    if isinstance(legend, list) and 0 <= code < len(legend):
        return legend[code]
    if isinstance(legend, dict):
        value = legend.get(str(code), legend.get(code))
        if value is not None:
            return value

    # Valeurs de map_generator.py fournies avec le projet.
    known_codes = {
        0: "water", 1: "ground", 2: "rock", 3: "gold",
        4: "tree", 5: "sheep", 6: "castle_purple", 7: "castle_yellow",
    }
    return known_codes.get(code)


def _is_walkable(state, rules, x, y):
    """
    Vérifie si une case est marchable.

    D'après les règles :
        ground = marchable
        tree   = marchable
        sheep  = marchable

    L'or, l'eau, les rochers et les châteaux
    ne sont pas marchables.
    """

    terrain = _legend_name(state, rules, x, y)
    walkable = rules.get("walkable", ["ground", "tree", "sheep"])

    # Les règles peuvent décrire les cases par nom ou par code numérique.
    if terrain in walkable:
        return True
    if isinstance(terrain, str):
        return terrain.lower() in {"ground", "grass", "tree", "sheep", "wood", "meat"}
    return False


def _inside(state, x, y):
    """
    Vérifie que [x, y] est à l'intérieur de la carte.
    """

    grid = state.get("grid", [])

    if not grid:
        return False

    return (
        0 <= y < len(grid)
        and 0 <= x < len(grid[y])
    )


# ============================================================
# OCCUPATION DES CASES
# ============================================================

def _occupied_cells(state, team):
    """
    Retourne les positions des unités ennemies.

    IMPORTANT :
    Le serveur autorise le chemin à travers nos propres unités,
    mais pas à travers les unités ennemies.

    On bloque donc seulement les ennemis.
    """

    enemy_cells = set()

    for unit in state.get("units", []):

        if unit.get("team") != team:
            enemy_cells.add(
                (
                    unit.get("x"),
                    unit.get("y")
                )
            )

    return enemy_cells


# ============================================================
# BFS
# ============================================================

def _bfs_reachable_goals(
    state,
    rules,
    start,
    goals,
    max_steps,
    blocked
):
    """Renvoie une case de destination qui fait réellement progresser le pion.

    L'ancienne version ne trouvait une destination que si le pion pouvait
    atteindre directement la case-goal en un seul tour. Si le château ou un
    gisement était plus loin que sa portée, elle renvoyait None et le pion
    restait immobile. Ici, le BFS cherche le chemin complet, puis renvoie le
    point le plus éloigné du chemin qui reste atteignable ce tour-ci.

    Les unités alliées peuvent être traversées, conformément aux règles,
    mais la destination elle-même doit être libre.
    """
    if max_steps <= 0 or not goals:
        return None

    goals = set(tuple(point) for point in goals)
    start = tuple(start)
    if start in goals:
        return None

    # Cases occupées à cet instant : interdites comme destination, mais les
    # alliés ne bloquent pas le chemin. Les ennemis sont fournis dans blocked.
    occupied = set()
    for unit in state.get("units", []):
        if not isinstance(unit, dict) or unit.get("hp", 1) <= 0:
            continue
        x, y = unit.get("x"), unit.get("y")
        if isinstance(x, int) and isinstance(y, int):
            occupied.add((x, y))

    queue = deque([start])
    distance = {start: 0}
    parent = {}
    reached_goal = None
    directions = ((1, 0), (-1, 0), (0, 1), (0, -1))

    while queue:
        point = queue.popleft()
        if point in goals:
            reached_goal = point
            break

        x, y = point
        for dx, dy in directions:
            nx, ny = x + dx, y + dy
            nxt = (nx, ny)
            if nxt in distance or not _inside(state, nx, ny):
                continue
            if not _is_walkable(state, rules, nx, ny):
                continue
            if nxt in blocked:
                continue
            distance[nxt] = distance[point] + 1
            parent[nxt] = point
            queue.append(nxt)

    if reached_goal is None:
        return None

    # Reconstituer le chemin du départ jusqu'au goal.
    path = []
    node = reached_goal
    while node != start:
        path.append(node)
        node = parent[node]
    path.reverse()
    if not path:
        return None

    # Prendre la case la plus avancée en un tour qui n'est pas occupée.
    # Le chemin peut traverser des alliés, mais le serveur refuse de s'arrêter
    # sur une case qu'une unité occupe encore.
    limit = min(max_steps, len(path))
    for index in range(limit - 1, -1, -1):
        destination = path[index]
        if destination not in occupied and destination not in blocked:
            return [destination[0], destination[1]]

    return None


# ============================================================
# DESTINATIONS AUTOUR D'UNE RESSOURCE
# ============================================================

def _free_walkable_cells_around_resource(
    state,
    rules,
    resource,
    occupied
):
    """
    Cherche les cases où le pion peut aller pour récolter.

    OR :
        une mine d'or n'est PAS marchable.
        Le pion doit donc aller sur une case voisine.

    BOIS / VIANDE :
        arbre et mouton sont marchables.
        Le pion peut donc être sur le gisement
        ou sur une case voisine.
    """

    rx = resource["x"]
    ry = resource["y"]

    resource_type = resource["resource"]

    candidates = []

    # Les 8 cases autour du gisement
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):

            x = rx + dx
            y = ry + dy

            if not _inside(state, x, y):
                continue

            # Une destination déjà occupée n'est pas valide
            if (x, y) in occupied:
                continue

            # La case doit être marchable
            if not _is_walkable(
                state,
                rules,
                x,
                y
            ):
                continue

            # Une mine d'or n'est jamais marchable
            if (
                resource_type == "gold"
                and (x, y) == (rx, ry)
            ):
                continue

            candidates.append((x, y))

    return candidates


# ============================================================
# CASES AUTOUR DU CHÂTEAU
# ============================================================

def _castle_cells(castle):
    """
    Retourne toutes les cases occupées par le château.

    Le château fait 4 x 2 cases.
    """

    cells = []

    x0 = castle["x"]
    y0 = castle["y"]

    width = castle.get("width", 4)
    height = castle.get("height", 2)

    for x in range(
        x0,
        x0 + width
    ):
        for y in range(
            y0,
            y0 + height
        ):
            cells.append((x, y))

    return cells


def _free_cells_around_castle(
    state,
    rules,
    castle,
    occupied
):
    """
    Cherche les cases marchables à côté du château.

    C'est là que le pion doit arriver pour pouvoir
    faire ensuite l'action deposit.
    """

    castle_cells = set(
        _castle_cells(castle)
    )

    candidates = set()

    for cx, cy in castle_cells:

        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):

                x = cx + dx
                y = cy + dy

                if not _inside(
                    state,
                    x,
                    y
                ):
                    continue

                # On ne peut pas aller dans le château
                if (x, y) in castle_cells:
                    continue

                # On évite une case occupée
                if (x, y) in occupied:
                    continue

                # La destination doit être marchable
                if not _is_walkable(
                    state,
                    rules,
                    x,
                    y
                ):
                    continue

                candidates.add(
                    (x, y)
                )

    return list(candidates)


# ============================================================
# CHOIX DES RESSOURCES ET RÉPARTITION DES OUVRIERS
# ============================================================

def _living_team_units(state, team):
    units = state.get("units", [])
    if isinstance(units, dict):
        units = list(units.values())
    if not isinstance(units, list):
        return []
    return [
        u for u in units
        if isinstance(u, dict)
        and u.get("team") == team
        and u.get("hp", 1) > 0
    ]


def _castle_threatened(state, team, rules):
    """Vrai si des combattants ennemis approchent du château."""
    castle = get_my_castle(state, team)
    if castle is None:
        return True

    max_hp = max(1, castle.get("max_hp", 1))
    hp_ratio = castle.get("hp", 0) / max_hp
    # Un château qui a déjà perdu 15 % de ses PV déclenche une alerte :
    # ne pas attendre qu'il soit presque détruit pour recruter des combattants.
    if hp_ratio <= 0.85:
        return True

    cx0, cy0 = castle.get("x", 0), castle.get("y", 0)
    cx1 = cx0 + max(1, int(castle.get("width", 4) or 4)) - 1
    cy1 = cy0 + max(1, int(castle.get("height", 2) or 2)) - 1
    unit_rules = rules.get("units", {})

    threat_units = state.get("units", [])
    if isinstance(threat_units, dict):
        threat_units = list(threat_units.values())
    for unit in threat_units if isinstance(threat_units, list) else []:
        if not isinstance(unit, dict) or unit.get("team") in (None, team):
            continue
        if unit.get("hp", 0) <= 0 or unit.get("type") == "pawn":
            continue
        ux, uy = unit.get("x"), unit.get("y")
        if not isinstance(ux, int) or not isinstance(uy, int):
            continue
        dx = max(cx0 - ux, 0, ux - cx1)
        dy = max(cy0 - uy, 0, uy - cy1)
        move = unit_rules.get(unit.get("type", ""), {}).get("move", 0)
        reach = max(8, int(move or 0) * 2 + 3)
        if max(dx, dy) <= reach:
            return True
    return False


def _resource_goals(state, team, rules):
    """Calcule les besoins d'approvisionnement, avec priorité à la viande.

    La viande est nécessaire à chaque recrutement et son coût augmente à
    chaque fois. On vise donc le coût actuel plus une petite réserve, sans
    retarder un recrutement : get_recruitment_actions ne vérifie que le coût
    réel du prochain recrutement.
    """
    team_state = state.get("teams", {}).get(team, {})
    stocks = get_my_resources(state, team)
    desired_type = choose_recruitment_type(state, team, rules)
    gold_cost = _get_gold_cost(rules, desired_type)
    next_meat = max(1, int(team_state.get("next_meat_cost", 1) or 1))

    # Réserve courte mais croissante : accélère les recrutements successifs
    # sans accumuler des stocks énormes avant d'utiliser le château.
    meat_reserve = max(2, min(5, (next_meat + 4) // 5))
    meat_goal = next_meat + meat_reserve
    # Toujours préparer l'or d'un guerrier au minimum ; si un lancier est
    # le prochain choix, prévoir son coût si possible.
    gold_goal = max(10, int(gold_cost or 0))

    castle = get_my_castle(state, team)
    wood_goal = 0
    if castle is not None:
        missing_hp = max(0, castle.get("max_hp", 0) - castle.get("hp", 0))
        if _castle_threatened(state, team, rules) and missing_hp >= 50:
            wood_goal = min(25, max(10, int(missing_hp)))

    projected = {
        "gold": stocks.get("gold", 0),
        "meat": stocks.get("meat", 0),
        "wood": stocks.get("wood", 0),
    }
    for pawn in get_my_pawns(state, team):
        carrying = pawn.get("carrying") or {}
        resource = _normalize_resource_type(carrying.get("resource"))
        amount = carrying.get("amount", 0)
        if resource in projected and isinstance(amount, (int, float)):
            projected[resource] += amount

    return {
        "gold": max(0, gold_goal - projected["gold"]) / max(1, gold_goal),
        "meat": max(0, meat_goal - projected["meat"]) / max(1, meat_goal),
        "wood": (max(0, wood_goal - projected["wood"]) / max(1, wood_goal)
                 if wood_goal > 0 else 0.0),
    }


def _should_return_loaded_meat(state, team, pawn, rules):
    """Force un dépôt de viande si l'or bloque les recrutements.

    La stratégie ne doit pas attendre que le stock atteigne le prochain coût
    de viande plus une réserve : sinon tous les pions peuvent rester sur la
    viande pendant que le château manque d'or. Dès qu'une quantité utile de
    viande est disponible/en transit, les porteurs rentrent et pourront ensuite
    être réaffectés à l'or.
    """
    carrying = pawn.get("carrying") or {}
    amount = carrying.get("amount", 0) or 0
    if amount <= 0 or _normalize_resource_type(carrying.get("resource")) != "meat":
        return False

    team_state = state.get("teams", {}).get(team, {})
    stocks = team_state.get("resources", {}) or {}
    # 5 or suffisent pour recruter un guerrier. Compter aussi l'or transporté :
    # s'il est déjà en route vers le château, on peut laisser les pions viande
    # terminer leur trajet normal.
    projected_gold = stocks.get("gold", 0) or 0
    next_meat = max(1, int(team_state.get("next_meat_cost", 1) or 1))
    projected_meat = stocks.get("meat", 0) or 0
    for unit in get_my_pawns(state, team):
        load = unit.get("carrying") or {}
        resource = _normalize_resource_type(load.get("resource"))
        amount = load.get("amount", 0) or 0
        if resource == "gold":
            projected_gold += amount
        elif resource == "meat":
            projected_meat += amount
    if projected_gold >= 5:
        return False

    switch_threshold = max(5, min(next_meat, 20) * 0.60)
    return projected_meat >= switch_threshold


def _wood_needed_for_repair(state, team, rules):
    """Faut-il des bûcherons ? Oui si le château a perdu WOOD_FROM_MISSING PV et que
    le bois en stock ne couvre pas les dégâts, ou en fin de partie pour garder une
    réserve (le départage au dernier tour se fait aux PV du château). Jamais quand
    un combattant ennemi est près du château."""
    castle = get_my_castle(state, team)
    if castle is None:
        return False
    missing_hp = max(0, int(castle.get("max_hp", 0) or 0) - int(castle.get("hp", 0) or 0))
    wood = get_my_resources(state, team).get("wood", 0)
    late = int(state.get("turn", 0) or 0) >= _turn(rules, LATE_GAME_TURN)
    repair_need = missing_hp >= WOOD_FROM_MISSING and wood < missing_hp
    reserve_need = late and wood < WOOD_RESERVE
    if not (repair_need or reserve_need):
        return False

    cx0, cy0 = castle.get("x", 0), castle.get("y", 0)
    cx1 = cx0 + max(1, int(castle.get("width", 4) or 4)) - 1
    cy1 = cy0 + max(1, int(castle.get("height", 2) or 2)) - 1
    unit_rules = rules.get("units", {})
    units = state.get("units", [])
    if isinstance(units, dict):
        units = list(units.values())
    for enemy in units if isinstance(units, list) else []:
        if not isinstance(enemy, dict) or enemy.get("team") in (None, team) or enemy.get("hp", 0) <= 0:
            continue
        kind = enemy.get("type", enemy.get("unit_type"))
        stats = unit_rules.get(kind, {})
        if kind == "pawn" or stats.get("attack", enemy.get("attack", 0)) <= 0:
            continue
        x, y = enemy.get("x"), enemy.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            continue
        dx = max(cx0 - x, 0, x - cx1)
        dy = max(cy0 - y, 0, y - cy1)
        # Si un combattant est proche, les pions restent concentrés sur
        # viande/or au lieu de partir chercher du bois.
        if max(dx, dy) <= 8:
            return False
    return True


# ============================================================
# RÉPARTITION DES PIONS ENTRE OR ET VIANDE (V16)
# ============================================================
#
# Chaque recrutement coûte de l'or (fixe selon l'unité) et de la viande
# (n viandes pour le n-ième recrutement). Au début, l'or pèse autant que la
# viande ; plus la partie avance, plus la viande domine. Les pions sont donc
# répartis selon les besoins des prochains recrutements, corrigés par les
# stocks et par la distance des gisements (un gisement lointain rapporte moins
# par pion). La répartition ne bouge que d'un pion par tour (progressivité),
# et un pion garde son rôle tant que son quota n'est pas dépassé (pas de
# demi-tour inutile).

# Nombre de recrutements à venir pris en compte pour estimer les besoins.
NEED_HORIZON = 5

# Stock d'or au-delà duquel les récolteurs d'or passent à la viande
# (et seuil de retour). Environ 4 lanciers d'avance.
GOLD_STOCK_HIGH = 45
GOLD_STOCK_LOW = 35

_GOLD_WORKERS_ON_MEAT = {}
_PAWN_ROLES = {}         # camp -> {id pion: ressource}
_LAST_GOLD_QUOTA = {}    # camp -> quota d'or du tour précédent
_INCOME_RATE = {}        # (camp, ressource) -> unités rapportées par pion et par tour


def _gold_workers_should_switch_to_meat(state, team):
    """Vrai quand l'or est largement en avance : plus aucun récolteur d'or.

    Hystérésis entre GOLD_STOCK_LOW et GOLD_STOCK_HIGH pour ne pas changer
    d'avis à chaque tour.
    """
    team_state = state.get("teams", {}).get(team, {})
    stocks = team_state.get("resources", {}) or {}
    gold = stocks.get("gold", 0) or 0
    switched = _GOLD_WORKERS_ON_MEAT.get(team, False)
    if gold >= GOLD_STOCK_HIGH:
        switched = True
    elif gold < GOLD_STOCK_LOW:
        switched = False
    _GOLD_WORKERS_ON_MEAT[team] = switched
    return switched


def _income_rate(state, team, rules, resource_type):
    """Unités rapportées par un pion et par tour sur le gisement le plus proche.

    Un aller-retour = trajet aller + 5 récoltes + trajet retour, l'arrivée et la
    première récolte (ou le retour et le dépôt) se faisant dans le même tour :
    cycle = capacité + 2 * trajet - 1 tours. Calculé une fois (carte fixe).
    """
    key = (team, resource_type)
    if key in _INCOME_RATE:
        return _INCOME_RATE[key]
    castle = get_my_castle(state, team)
    capacity = int(rules.get("carry_capacity", 5) or 5)
    pawn_move = int(rules.get("units", {}).get("pawn", {}).get("move", 4) or 4)
    deposits = get_resource_positions(state, resource_type)
    if castle is None or not deposits:
        return 0.0
    # BFS depuis les cases de dépôt autour du château.
    start = _free_cells_around_castle(state, rules, castle, set())
    distance = {point: 0 for point in start}
    queue = deque(start)
    while queue:
        x, y = queue.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            point = (x + dx, y + dy)
            if point in distance or not _inside(state, *point) or not _is_walkable(state, rules, *point):
                continue
            distance[point] = distance[(x, y)] + 1
            queue.append(point)
    best = None
    for deposit in deposits:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                d = distance.get((deposit["x"] + dx, deposit["y"] + dy))
                if d is not None and (best is None or d < best):
                    best = d
    if best is None:
        rate = 0.0
    else:
        trip = max(1, -(-best // pawn_move))
        rate = capacity / (capacity + 2 * trip - 1)
    _INCOME_RATE[key] = rate
    return rate


def _upcoming_needs(state, team, rules):
    """Or et viande nécessaires pour les NEED_HORIZON prochains recrutements."""
    team_state = state.get("teams", {}).get(team, {})
    recruited = int(team_state.get("recruited", 0) or 0)
    meat_per_recruit = int(rules.get("meat_per_recruit", 1) or 1)
    alive = _living_team_units(state, team)
    pawns = sum(1 for u in alive if u.get("type") == "pawn")
    monks = sum(1 for u in alive if u.get("type") == "monk")
    gold_need = 0
    meat_need = 0
    first = choose_recruitment_type(state, team, rules)
    for i in range(NEED_HORIZON):
        rank = recruited + 1 + i
        if i == 0 and first is not None:
            # Le prochain recrutement est celui que choose_recruitment_type demandera
            # (par exemple un lancier quand le château est menacé).
            kind = first
            pawns += kind == "pawn"
            monks += kind == "monk"
        elif pawns < PAWN_TARGET:
            kind = "pawn"
            pawns += 1
        elif rank >= MONK_FROM_RECRUIT and monks < MAX_MONKS:
            kind = "monk"
            monks += 1
        else:
            kind = "lancer"
        gold_need += _get_gold_cost(rules, kind)
        meat_need += rank * meat_per_recruit
    return gold_need, meat_need


def _target_gold_workers(state, team, rules, workers, projected):
    """Nombre de pions à mettre sur l'or parmi `workers` récolteurs or/viande."""
    if workers <= 0:
        return 0
    gold_need, meat_need = _upcoming_needs(state, team, rules)
    rate_gold = _income_rate(state, team, rules, "gold") or 0.01
    rate_meat = _income_rate(state, team, rules, "meat") or 0.01
    # Besoin courant + manque par rapport au stock, en « tours de pion ».
    gold_load = (gold_need + max(0, gold_need - projected["gold"])) / rate_gold
    meat_load = (meat_need + max(0, meat_need - projected["meat"])) / rate_meat
    share = gold_load / max(1e-9, gold_load + meat_load)
    target = int(round(workers * share))
    if gold_need > projected["gold"]:
        target = max(1, target)        # ne jamais laisser l'or à l'abandon s'il manque
        if projected["meat"] >= meat_need:
            # La viande est couverte, l'or bloque : au moins la moitié des pions sur l'or.
            target = max(target, -(-workers // 2))
    if workers >= 2 and projected["meat"] < meat_need:
        target = min(target, workers - 1)
    target = max(0, min(workers, target))
    # Progressivité : au plus un pion de différence par tour.
    previous = _LAST_GOLD_QUOTA.get(team)
    if previous is not None:
        target = max(previous - 1, min(previous + 1, target))
    _LAST_GOLD_QUOTA[team] = target
    return target


def _worker_resource_assignments(state, team, rules):
    """Rôle de chaque pion (gold / meat / wood).

    - Un pion chargé garde la ressource qu'il porte jusqu'au dépôt.
    - Le bois n'occupe qu'un pion, et seulement si le château doit être réparé.
    - Le reste est partagé entre or et viande selon _target_gold_workers.
    - Un pion garde son rôle précédent tant que le quota le permet.
    """
    final = _FINAL.get(team, {})
    sacrificed = _SACRIFICE.get(team, set()) | final.get("guards", set()) | final.get("launched", set())
    pawns = sorted((p for p in get_my_pawns(state, team) if p.get("id") not in sacrificed),
                   key=lambda u: u.get("id", 0))
    if not pawns:
        return {}
    available = {kind: bool(get_resource_positions(state, kind)) for kind in ("meat", "gold", "wood")}
    stocks = get_my_resources(state, team)
    projected = {kind: stocks.get(kind, 0) or 0 for kind in ("meat", "gold", "wood")}
    for pawn in pawns:
        carrying = pawn.get("carrying") or {}
        kind = _normalize_resource_type(carrying.get("resource"))
        if kind in projected:
            projected[kind] += carrying.get("amount", 0) or 0

    n = len(pawns)
    wood = 0
    if available["wood"] and n >= 6 and _wood_needed_for_repair(state, team, rules):
        castle = get_my_castle(state, team)
        missing = castle.get("max_hp", 0) - castle.get("hp", 0) if castle else 0
        wood = 2 if missing >= WOOD_TWO_WORKERS_FROM else 1
    workers = n - wood
    if _gold_workers_should_switch_to_meat(state, team) or not available["gold"]:
        gold = 0
        _LAST_GOLD_QUOTA[team] = 0
    elif not available["meat"]:
        gold = workers
    else:
        gold = _target_gold_workers(state, team, rules, workers, projected)
    quotas = {"gold": gold, "meat": workers - gold, "wood": wood}

    previous = _PAWN_ROLES.get(team, {})
    assignments = {}
    counts = {"meat": 0, "gold": 0, "wood": 0}
    # 1. Les pions chargés terminent leur livraison.
    for pawn in pawns:
        carrying = pawn.get("carrying") or {}
        kind = _normalize_resource_type(carrying.get("resource"))
        if (carrying.get("amount", 0) or 0) > 0 and kind in counts:
            assignments[pawn["id"]] = kind
            counts[kind] += 1
    # 2. Les pions vides gardent leur rôle si le quota le permet.
    free = []
    for pawn in pawns:
        if pawn["id"] in assignments:
            continue
        role = previous.get(pawn["id"])
        if role in counts and available[role] and counts[role] < quotas[role]:
            assignments[pawn["id"]] = role
            counts[role] += 1
        else:
            free.append(pawn)
    # 3. Les autres vont là où il manque le plus de monde (viande en cas d'égalité).
    for pawn in free:
        choices = [kind for kind in ("meat", "gold", "wood") if available[kind]] or ["meat"]
        chosen = max(choices, key=lambda kind: (quotas[kind] - counts[kind], kind == "meat"))
        assignments[pawn["id"]] = chosen
        counts[chosen] += 1
    _PAWN_ROLES[team] = assignments
    return assignments


def _resource_priority(state, team, rules, unit_count):
    """Compatibilité avec les autres fonctions : renvoie la ressource prioritaire."""
    assignments = _worker_resource_assignments(state, team, rules)
    counts = {"gold": 0, "meat": 0, "wood": 0}
    for kind in assignments.values():
        kind = _normalize_resource_type(kind)
        if kind in counts:
            counts[kind] += 1
    # Préférer le besoin qui reçoit actuellement le plus d'ouvriers.
    return max(counts, key=counts.get) if counts else "gold"


# ============================================================
# DÉPLACEMENTS ÉCONOMIQUES
# ============================================================

def get_economy_moves(state, team, rules):
    """Déplace les travailleurs vers les ressources et vers le château.

    Les pions remplissent leur chargement (jusqu'à carry_capacity) avant de
    rentrer, sauf s'ils sont déjà à portée de dépôt. Cela évite les allers-
    retours inefficaces après une seule unité récoltée.
    """
    moves = []
    castle = get_my_castle(state, team)
    if castle is None:
        return moves
    sacrificed = _update_sacrifice(state, team, rules)
    for order in _sacrifice_moves_and_actions(state, team, rules, sacrificed, "moves"):
        moves.append({"unit": order["unit"], "to": order["to"]})
    pawns = sorted((p for p in get_my_pawns(state, team) if p.get("id") not in sacrificed),
                   key=lambda u: u.get("id", 0))

    enemy_occupied = _occupied_cells(state, team)
    all_occupied = {
        (unit.get("x"), unit.get("y"))
        for unit in state.get("units", [])
        if isinstance(unit, dict)
        and unit.get("x") is not None and unit.get("y") is not None
        and unit.get("hp", 1) > 0
    }
    reserved_destinations = set()
    guards, strikers = _final_roles(state, team, rules)
    final_moves, final_handled = _final_moves(state, team, rules, guards, strikers, reserved_destinations)
    moves.extend(final_moves)
    pawns = [p for p in pawns if p.get("id") not in final_handled]
    pawn_move = int(rules.get("units", {}).get("pawn", {}).get("move", 4) or 4)
    carry_capacity = int(rules.get("carry_capacity", 5) or 5)
    assignments = _worker_resource_assignments(state, team, rules)
    gold_workers_switch_to_meat = _gold_workers_should_switch_to_meat(state, team)

    # Ennemis capables de combattre : on évite d'envoyer les travailleurs
    # vers une zone où ils risquent d'être attaqués au prochain tour.
    unit_rules = rules.get("units", {})
    enemy_fighters = []
    for enemy in state.get("units", []):
        if not isinstance(enemy, dict) or enemy.get("team") in (None, team) or enemy.get("hp", 0) <= 0:
            continue
        enemy_type = enemy.get("type", enemy.get("unit_type"))
        stats = unit_rules.get(enemy_type, {})
        attack = stats.get("attack", enemy.get("attack", 0))
        if enemy_type == "pawn" or attack <= 0:
            continue
        enemy_fighters.append({
            "x": enemy.get("x", 0),
            "y": enemy.get("y", 0),
            "move": int(stats.get("move", 0) or 0),
            "range": int(stats.get("range", 0) or 0),
        })

    def danger_clearance(x, y):
        """Marge au-delà de la portée de déplacement + attaque ennemie."""
        if not enemy_fighters:
            return 999
        return min(
            max(abs(x - enemy["x"]), abs(y - enemy["y"])) - enemy["move"] - enemy["range"] - 1
            for enemy in enemy_fighters
        )

    def retreat_destination(pawn):
        """Trouve une case atteignable plus sûre, en préférant rester près du château."""
        start = (pawn["x"], pawn["y"])
        queue = deque([start])
        steps = {start: 0}
        directions = ((1, 0), (-1, 0), (0, 1), (0, -1))
        while queue:
            x, y = queue.popleft()
            if steps[(x, y)] >= pawn_move:
                continue
            for dx, dy in directions:
                nx, ny = x + dx, y + dy
                point = (nx, ny)
                if point in steps or not _inside(state, nx, ny):
                    continue
                if not _is_walkable(state, rules, nx, ny) or point in enemy_occupied:
                    continue
                steps[point] = steps[(x, y)] + 1
                queue.append(point)

        current_clearance = danger_clearance(*start)
        candidates = []
        for point, distance in steps.items():
            if point == start or point in all_occupied or point in reserved_destinations:
                continue
            clearance = danger_clearance(*point)
            if clearance <= current_clearance:
                continue
            px, py = point
            castle_distance = min(
                chebyshev_distance(px, py, cx, cy)
                for cx, cy in _castle_cells(castle)
            )
            # Prioriser la sortie de la zone de menace, puis rester proche du château.
            candidates.append((clearance, -castle_distance, -distance, point))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        point = candidates[0][3]
        return [point[0], point[1]]

    def move_toward_resource(pawn, resource_type):
        start = (pawn["x"], pawn["y"])
        resources = get_resource_positions(state, resource_type)
        if not resources:
            return None
        resources = sorted(resources, key=lambda r: chebyshev_distance(
            start[0], start[1], r["x"], r["y"]))
        for resource in resources:
            goals = _free_walkable_cells_around_resource(
                state, rules, resource, all_occupied | reserved_destinations)
            destination = _bfs_reachable_goals(
                state, rules, start, goals, pawn_move, enemy_occupied)
            if destination is not None:
                return destination
        return None

    def move_toward_castle(pawn):
        if _is_at_castle_range(pawn, castle):
            return None
        goals = _free_cells_around_castle(
            state, rules, castle, all_occupied | reserved_destinations)
        return _bfs_reachable_goals(
            state, rules, (pawn["x"], pawn["y"]), goals,
            pawn_move, enemy_occupied)

    for pawn in pawns:
        carrying = pawn.get("carrying") or {}
        amount = carrying.get("amount", 0) or 0

        # À portée du château avec un chargement : rester en place pour déposer.
        if amount > 0 and _is_at_castle_range(pawn, castle):
            continue

        # Si un combattant menace le pion, un pion chargé priorise le retour
        # au château (au lieu de faire des allers-retours de repli). Un pion
        # vide, lui, se replie et interrompt sa route vers le gisement.
        start = (pawn.get("x"), pawn.get("y"))
        threatened_worker = any(
            max(abs(start[0] - enemy["x"]), abs(start[1] - enemy["y"]))
            <= enemy["move"] + enemy["range"] + 1
            for enemy in enemy_fighters
        )
        if threatened_worker:
            if amount > 0:
                retreat = move_toward_castle(pawn)
            else:
                retreat = retreat_destination(pawn)
            if retreat is not None and tuple(retreat) not in reserved_destinations:
                moves.append({"unit": pawn["id"], "to": retreat})
                reserved_destinations.add(tuple(retreat))
            # Si aucune case sûre n'est accessible, ne pas avancer vers une
            # ressource exposée. L'action économique vérifiera aussi le danger.
            continue

        destination = None
        if amount > 0:
            carried_type = _normalize_resource_type(carrying.get("resource"))
            # Quand le stock d'or a atteint le seuil haut (32), les porteurs
            # d'or rentrent déposer leur charge, puis repartent vers la viande.
            if carried_type == "gold" and gold_workers_switch_to_meat:
                destination = move_toward_castle(pawn)
            else:
                # Si l'or bloque le recrutement, rapatrier une charge de viande
                # dès qu'il existe déjà une quantité utile de viande en stock/en transit.
                unload_early = _should_return_loaded_meat(state, team, pawn, rules)
                if unload_early:
                    destination = move_toward_castle(pawn)
                # Continuer la récolte uniquement si les stocks ont encore besoin
                # de cette ressource et que le chargement n'est pas à déposer tôt.
                if destination is None and not unload_early and amount < carry_capacity and carried_type in ("gold", "wood", "meat"):
                    nearby_same = any(
                        _is_at_resource_range(pawn, resource)
                        for resource in get_resource_positions(state, carried_type)
                    )
                    if nearby_same:
                        # Rester sur place : get_economy_actions récoltera ce tour.
                        continue
                    destination = move_toward_resource(pawn, carried_type)
                # Chargement plein, dépôt anticipé, ressource introuvable ou aucun chemin : retour château.
                if destination is None:
                    destination = move_toward_castle(pawn)
        else:
            resource_type = assignments.get(pawn.get("id"), "gold")
            resources = get_resource_positions(state, resource_type)
            if not resources:
                resources = get_resource_positions(state)

            # Si déjà en position de récolte, ne pas s'éloigner juste avant gather.
            if any(_is_at_resource_range(pawn, resource) for resource in resources):
                continue

            destination = move_toward_resource(pawn, resource_type)
            if destination is None:
                # Sur carte atypique, tenter n'importe quel gisement disponible.
                all_resources = get_resource_positions(state)
                all_resources = sorted(all_resources, key=lambda r: chebyshev_distance(
                    pawn["x"], pawn["y"], r["x"], r["y"]))
                for resource in all_resources:
                    goals = _free_walkable_cells_around_resource(
                        state, rules, resource, all_occupied | reserved_destinations)
                    destination = _bfs_reachable_goals(
                        state, rules, (pawn["x"], pawn["y"]), goals,
                        pawn_move, enemy_occupied)
                    if destination is not None:
                        break

        if destination is not None and tuple(destination) not in reserved_destinations:
            moves.append({"unit": pawn["id"], "to": destination})
            reserved_destinations.add(tuple(destination))

    return moves


# ============================================================
# VÉRIFICATION DE PROXIMITÉ
# ============================================================

def _is_at_resource_range(
    pawn,
    resource
):
    """
    Vérifie si le pion est à portée de récolte.

    La récolte utilise la distance de Chebyshev <= 1.
    """

    return chebyshev_distance(
        pawn["x"],
        pawn["y"],
        resource["x"],
        resource["y"]
    ) <= 1


def _is_at_castle_range(
    pawn,
    castle
):
    """
    Vérifie si le pion est à portée de dépôt.

    On compare la position du pion avec
    les différentes cases du château.
    """

    for cx, cy in _castle_cells(
        castle
    ):

        if chebyshev_distance(
            pawn["x"],
            pawn["y"],
            cx,
            cy
        ) <= 1:

            return True

    return False


# ============================================================
# ACTIONS ÉCONOMIQUES
# ============================================================

def _pawn_likely_to_die_before_economy(state, team, pawn, rules):
    """Évite de planifier gather/deposit si les ennemis en portée peuvent le tuer.

    Le combat est résolu avant gather/deposit. Si les dégâts potentiels des
    combattants ennemis déjà à portée suffisent à tuer le pion, son action
    économique sera refusée par le serveur ; on l'évite donc.
    """
    unit_rules = rules.get("units", {})
    damage = 0
    px, py = pawn.get("x"), pawn.get("y")
    if not isinstance(px, int) or not isinstance(py, int):
        return False

    units = state.get("units", [])
    if isinstance(units, dict):
        units = list(units.values())
    for enemy in units if isinstance(units, list) else []:
        if not isinstance(enemy, dict) or enemy.get("team") in (None, team):
            continue
        if enemy.get("hp", 0) <= 0:
            continue
        enemy_type = enemy.get("type", enemy.get("unit_type"))
        stats = unit_rules.get(enemy_type, {})
        attack = stats.get("attack", enemy.get("attack", 0))
        attack_range = stats.get("range", enemy.get("range", 0))
        ex, ey = enemy.get("x"), enemy.get("y")
        if not isinstance(ex, int) or not isinstance(ey, int) or attack <= 0:
            continue
        if max(abs(px - ex), abs(py - ey)) <= attack_range:
            damage += attack
    return damage >= pawn.get("hp", 0) and damage > 0


def get_economy_actions(
    state,
    team,
    rules
):
    """
    Génère les actions économiques des pions.

    Cette fonction fait UNIQUEMENT :

        - deposit
        - gather

    Elle ne fait PAS :

        - attack
        - heal
    """

    actions = []

    pawns = get_my_pawns(
        state,
        team
    )

    castle = get_my_castle(
        state,
        team
    )

    if castle is None:
        return actions

    carry_capacity = rules.get("carry_capacity", 5)
    resource_assignments = _worker_resource_assignments(state, team, rules)
    gold_workers_switch_to_meat = _gold_workers_should_switch_to_meat(state, team)
    sacrificed = _update_sacrifice(state, team, rules)
    actions.extend(_sacrifice_moves_and_actions(state, team, rules, sacrificed, "actions"))
    final_actions, final_handled = _final_actions(state, team, rules)
    actions.extend(final_actions)

    for pawn in pawns:
        if pawn.get("id") in sacrificed or pawn.get("id") in final_handled:
            continue

        # Le combat est résolu avant l'économie : ne pas envoyer une action
        # inutile si les ennemis déjà à portée peuvent tuer ce pion ce tour.
        if _pawn_likely_to_die_before_economy(state, team, pawn, rules):
            continue

        carrying = pawn.get(
            "carrying"
        )

        # ====================================================
        # PRIORITÉ 1 : DÉPOSER
        # ====================================================

        if (
            carrying is not None
            and carrying.get(
                "amount",
                0
            ) > 0
            and _is_at_castle_range(
                pawn,
                castle
            )
        ):

            actions.append(
                {
                    "type": "deposit",
                    "unit": pawn["id"]
                }
            )

            # Un pion ne peut faire qu'une action par tour.
            continue

        # Si l'or a atteint le seuil de 32, un porteur d'or doit rapporter
        # sa charge, mais ne doit pas continuer à récolter de l'or ce tour-ci.
        carrying_now = carrying or {}
        if (gold_workers_switch_to_meat
                and _normalize_resource_type(carrying_now.get("resource")) == "gold"
                and (carrying_now.get("amount", 0) or 0) > 0):
            continue

        # Si ce porteur doit rapporter sa viande pour débloquer le recrutement,
        # ne pas consommer son action pour re-récolter avant son dépôt.
        if _should_return_loaded_meat(state, team, pawn, rules):
            continue

        # ====================================================
        # PRIORITÉ 2 : RÉCOLTER
        # ====================================================

        if (
            carrying is not None
            and carrying.get(
                "amount",
                0
            ) >= carry_capacity
        ):
            continue

        resource_type = resource_assignments.get(pawn.get("id"), "gold")

        # Si le pion transporte déjà une ressource,
        # il doit continuer à récolter la même ressource.
        if (
            carrying is not None
            and carrying.get(
                "amount",
                0
            ) > 0
        ):

            resource_type = _normalize_resource_type(carrying.get("resource"))

        nearby_resources = [
            resource
            for resource in get_resource_positions(state, resource_type)
            if _is_at_resource_range(pawn, resource)
        ]

        # Ne récolter que la ressource attribuée (ou celle déjà transportée).
        # Cela évite que tous les pions récoltent le premier gisement rencontré.

        if nearby_resources:

            resource = nearby_resources[0]

            actions.append(
                {
                    "type": "gather",
                    "unit": pawn["id"],
                    "target": [
                        resource["x"],
                        resource["y"]
                    ]
                }
            )

    return actions


# ============================================================
# CHOIX DU RECRUTEMENT
# ============================================================

def _composition_targets(state, rules=None):
    """(pions visés, archers visés) : en fin de partie, FLANK_ARCHERS pions laissent
    leur place à autant d'archers de contournement."""
    if int(state.get("turn", 0) or 0) >= _turn(rules, FLANK_START):
        return PAWN_TARGET - FLANK_ARCHERS, ARCHER_GUARDS + FLANK_ARCHERS
    return PAWN_TARGET, ARCHER_GUARDS


def _update_sacrifice(state, team, rules):
    """Désigne les pions à sacrifier pour libérer de la place aux archers de contournement."""
    alive = _living_team_units(state, team)
    alive_ids = {u.get("id") for u in alive}
    chosen = _SACRIFICE.setdefault(team, set())
    chosen &= alive_ids
    if int(state.get("turn", 0) or 0) < _turn(rules, FLANK_START):
        return chosen
    pawn_target, archer_target = _composition_targets(state, rules)
    pawns = [u for u in alive if u.get("type") == "pawn"]
    archers = sum(1 for u in alive if u.get("type") == "archer")
    full = len(alive) >= int(rules.get("max_units", 40) or 40)
    missing_archers = archer_target - archers
    surplus = len(pawns) - pawn_target
    wanted = min(missing_archers, surplus) if full else 0
    if wanted > len(chosen):
        # Les pions vides d'abord (on ne perd pas de cargaison), les plus récents.
        candidates = sorted((p for p in pawns if p.get("id") not in chosen),
                            key=lambda p: (1 if p.get("carrying") else 0, -p.get("id", 0)))
        for p in candidates[: wanted - len(chosen)]:
            chosen.add(p.get("id"))
    return chosen


def _sacrifice_moves_and_actions(state, team, rules, chosen, phase):
    """Les pions sacrifiés marchent vers le château adverse (la garde adverse les
    tue) ; s'ils arrivent au contact, ils frappent le château."""
    enemy_castle = next((b for b in state.get("buildings", []) if b.get("team") != team), None)
    if enemy_castle is None:
        return []
    out = []
    pawn_move = int(rules.get("units", {}).get("pawn", {}).get("move", 4) or 4)
    occupied = {(u.get("x"), u.get("y")) for u in state.get("units", []) if u.get("hp", 1) > 0}
    for pawn in get_my_pawns(state, team):
        if pawn.get("id") not in chosen:
            continue
        adjacent = _is_at_castle_range(pawn, enemy_castle)
        if phase == "actions":
            if adjacent:
                out.append({"type": "attack", "unit": pawn["id"], "target": enemy_castle["id"]})
            continue
        if adjacent:
            continue
        goals = _free_cells_around_castle(state, rules, enemy_castle, occupied)
        destination = _bfs_reachable_goals(state, rules, (pawn["x"], pawn["y"]), goals,
                                           pawn_move, _occupied_cells(state, team))
        if destination is not None and tuple(destination) not in occupied:
            out.append({"type": "move_placeholder", "unit": pawn["id"], "to": destination})
            occupied.add(tuple(destination))
    return out


# ============================================================
# COUP FINAL : GARDE DU CHÂTEAU ET FRAPPE DES PIONS
# ============================================================

def _bfs_steps(state, rules, start, goals, blocked):
    """Nombre de pas du plus court chemin de start vers goals (None si inaccessible)."""
    goals = set(tuple(g) for g in goals)
    start = tuple(start)
    if start in goals:
        return 0
    seen = {start: 0}
    queue = deque([start])
    while queue:
        x, y = queue.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nxt = (x + dx, y + dy)
            if nxt in seen or nxt in blocked or not _inside(state, nxt[0], nxt[1]):
                continue
            if not _is_walkable(state, rules, nxt[0], nxt[1]):
                continue
            seen[nxt] = seen[(x, y)] + 1
            if nxt in goals:
                return seen[nxt]
            queue.append(nxt)
    return None


def _final_roles(state, team, rules):
    """(gardes, frappeurs) à partir de FINAL_PHASE_TURN : les FINAL_GUARDS pions les plus
    proches du château restent pour la réparation du dernier tour, les autres partiront
    frapper le château adverse."""
    if int(state.get("turn", 0) or 0) < _turn(rules, FINAL_PHASE_TURN):
        return set(), set()
    castle = get_my_castle(state, team)
    sacrificed = _SACRIFICE.get(team, set())
    pawns = [p for p in get_my_pawns(state, team) if p.get("id") not in sacrificed]
    memo = _FINAL.setdefault(team, {"guards": set(), "launched": set()})
    alive = {p.get("id") for p in pawns}
    memo["guards"] &= alive
    memo["launched"] &= alive
    if castle is not None and len(memo["guards"]) < FINAL_GUARDS:
        candidates = sorted(
            (p for p in pawns if p.get("id") not in memo["guards"] and p.get("id") not in memo["launched"]),
            key=lambda p: min(chebyshev_distance(p["x"], p["y"], cx, cy) for cx, cy in _castle_cells(castle)))
        for p in candidates[: FINAL_GUARDS - len(memo["guards"])]:
            memo["guards"].add(p.get("id"))
    return set(memo["guards"]), alive - memo["guards"]


def _final_moves(state, team, rules, guards, strikers, reserved):
    """Déplacements du coup final. Renvoie (moves, ids des pions pris en charge)."""
    moves, handled = [], set()
    castle = get_my_castle(state, team)
    enemy_castle = next((b for b in state.get("buildings", []) if b.get("team") != team), None)
    memo = _FINAL.get(team)
    if castle is None or memo is None:
        return moves, handled
    turn = int(state.get("turn", 0) or 0)          # phase de déplacement : tour précédent
    max_turns = int(rules.get("max_turns", 500) or 500)
    pawn_move = int(rules.get("units", {}).get("pawn", {}).get("move", 4) or 4)
    carry = int(rules.get("carry_capacity", 5) or 5)
    occupied = {(u.get("x"), u.get("y")) for u in state.get("units", []) if u.get("hp", 1) > 0}
    enemy_occupied = _occupied_cells(state, team)

    def go(pawn, goals):
        dest = _bfs_reachable_goals(state, rules, (pawn["x"], pawn["y"]), goals, pawn_move, enemy_occupied)
        if dest is not None and tuple(dest) not in reserved:
            moves.append({"unit": pawn["id"], "to": dest})
            reserved.add(tuple(dest))

    for pawn in get_my_pawns(state, team):
        pid = pawn.get("id")
        carrying = pawn.get("carrying") or {}
        amount = carrying.get("amount", 0) or 0
        kind = _normalize_resource_type(carrying.get("resource"))

        # Frappeurs : ils travaillent jusqu'au dernier moment, puis foncent (4 cases par
        # tour) pour arriver au contact du château adverse au tour PAWN_STRIKE_TURN.
        if pid in strikers and enemy_castle is not None:
            if pid not in memo["launched"]:
                goals = _free_cells_around_castle(state, rules, enemy_castle, set())
                steps = _bfs_steps(state, rules, (pawn["x"], pawn["y"]), goals, set())
                if steps is not None and turn + 1 + -(-steps // pawn_move) >= _turn(rules, PAWN_STRIKE_TURN):
                    memo["launched"].add(pid)
            if pid in memo["launched"]:
                handled.add(pid)
                if not _is_at_castle_range(pawn, enemy_castle):
                    go(pawn, _free_cells_around_castle(state, rules, enemy_castle, occupied | reserved))
            continue

        # Gardes : un plein de bois chacun, puis ils attendent au château le dernier tour.
        if pid in guards:
            handled.add(pid)
            returning = ((kind == "wood" and amount >= carry) or (amount > 0 and kind != "wood")
                         or turn + 1 >= max_turns - GUARD_RETURN_TURNS)
            if returning:
                if not _is_at_castle_range(pawn, castle):
                    go(pawn, _free_cells_around_castle(state, rules, castle, occupied | reserved))
                continue
            trees = get_resource_positions(state, "wood")
            if any(_is_at_resource_range(pawn, r) for r in trees):
                continue
            for tree in sorted(trees, key=lambda r: chebyshev_distance(pawn["x"], pawn["y"], r["x"], r["y"]))[:12]:
                goals = _free_walkable_cells_around_resource(state, rules, tree, occupied | reserved)
                if _bfs_reachable_goals(state, rules, (pawn["x"], pawn["y"]), goals, pawn_move, enemy_occupied):
                    go(pawn, goals)
                    break
    return moves, handled


def _final_actions(state, team, rules):
    """Actions du coup final. Renvoie (actions, ids des pions pris en charge)."""
    actions, handled = [], set()
    guards, strikers = _final_roles(state, team, rules)
    memo = _FINAL.get(team)
    castle = get_my_castle(state, team)
    if memo is None or castle is None:
        return actions, handled
    enemy_castle = next((b for b in state.get("buildings", []) if b.get("team") != team), None)
    turn = int(state.get("turn", 0) or 0)          # phase d'action : tour en cours
    max_turns = int(rules.get("max_turns", 500) or 500)
    carry = int(rules.get("carry_capacity", 5) or 5)
    for pawn in get_my_pawns(state, team):
        pid = pawn.get("id")
        carrying = pawn.get("carrying") or {}
        amount = carrying.get("amount", 0) or 0
        kind = _normalize_resource_type(carrying.get("resource"))
        if pid in memo["launched"]:
            handled.add(pid)
            if enemy_castle is not None and _is_at_castle_range(pawn, enemy_castle):
                actions.append({"type": "attack", "unit": pid, "target": enemy_castle["id"]})
            continue
        if pid not in guards:
            continue
        handled.add(pid)
        if amount > 0 and _is_at_castle_range(pawn, castle):
            # Le bois reste dans les mains jusqu'au dernier tour (le dépôt passe avant
            # la réparation, qui consomme tout le stock d'un coup).
            if kind != "wood" or turn >= max_turns:
                actions.append({"type": "deposit", "unit": pid})
            continue
        if amount >= carry or (amount > 0 and kind != "wood") or turn >= max_turns - GUARD_RETURN_TURNS:
            continue
        trees = [r for r in get_resource_positions(state, "wood") if _is_at_resource_range(pawn, r)]
        if trees and not _pawn_likely_to_die_before_economy(state, team, pawn, rules):
            actions.append({"type": "gather", "unit": pid, "target": [trees[0]["x"], trees[0]["y"]]})
    return actions, handled


def choose_recruitment_type(state, team, rules):
    """V16 : beaucoup de pions tôt (la viande ne coûte presque rien au début),
    puis des lanciers.

    À budget égal, le lancier bat le guerrier dès le début de partie, et l'écart
    grandit avec le coût de la viande. Le guerrier ne sert que de bouche-trou :
    get_recruitment_actions le prend quand l'or manque pour un lancier, au lieu
    de perdre le tour de recrutement.
    """
    team_state = state.get("teams", {}).get(team, {})
    alive = _living_team_units(state, team)
    pawns = sum(1 for u in alive if u.get("type", u.get("unit_type")) == "pawn")
    monks = sum(1 for u in alive if u.get("type", u.get("unit_type")) == "monk")
    archers = sum(1 for u in alive if u.get("type", u.get("unit_type")) == "archer")

    # Danger réel : utiliser le recrutement disponible pour renforcer l'armée.
    if _castle_threatened(state, team, rules):
        # P3 : à court d'or pour un lancier, on continue les pions jusqu'à l'objectif.
        gold_now = (team_state.get("resources", {}) or {}).get("gold", 0) or 0
        if pawns < PAWN_TARGET and gold_now < _get_gold_cost(rules, "lancer"):
            return "pawn"
        return "lancer"

    pawn_target, archer_target = _composition_targets(state, rules)

    # Noyau économique : les premiers recrutements sont les moins chers en
    # viande, c'est le meilleur moment pour multiplier les récolteurs.
    if pawns < pawn_target:
        return "pawn"

    # Un pion de plus pour le bois quand le château doit être réparé.
    if _wood_needed_for_repair(state, team, rules) and pawns < pawn_target + 1:
        return "pawn"

    # Garde permanente du château (et archers de contournement en fin de partie),
    # en alternance avec les lanciers : les archers seuls ne tiennent pas une attaque.
    lancers = sum(1 for u in alive if u.get("type", u.get("unit_type")) == "lancer")
    if archers < archer_target and (archers < lancers + ARCHER_LEAD or lancers >= ARCHER_LEAD_UNTIL):
        return "archer"
    if archers < archer_target and lancers < LANCER_TARGET:
        return "lancer"

    # Moines : pas avant le 25e recrutement, MAX_MONKS au maximum.
    next_rank = int(team_state.get("recruited", 0) or 0) + 1
    if next_rank >= MONK_FROM_RECRUIT and monks < MAX_MONKS:
        return "monk"

    # Armée de campagne plafonnée à LANCER_TARGET (les guerriers bouche-trou comptent).
    field = sum(1 for u in alive if u.get("type", u.get("unit_type")) in ("lancer", "warrior"))
    if field >= LANCER_TARGET:
        return None
    return "lancer"

def get_recruitment_actions(state, team, rules, planned_economy_actions=None):
    """Génère un recrutement valide au maximum, en utilisant les dépôts prévus.

    Le serveur limite à un recrutement par tour. Si le type prioritaire (par
    exemple un lancier) est trop cher en or, la stratégie essaie un guerrier
    moins cher au lieu de perdre le tour, tout en respectant le coût en viande.
    """
    team_state = state.get("teams", {}).get(team, {})
    stocks = dict(team_state.get("resources", {"gold": 0, "wood": 0, "meat": 0}))

    # Un dépôt est résolu avant le recrutement dans template.py : ajouter les
    # ressources qui seront déposées, pour ne pas perdre un tour sans raison.
    if planned_economy_actions:
        deposit_ids = {a.get("unit") for a in planned_economy_actions if a.get("type") == "deposit"}
        for pawn in get_my_pawns(state, team):
            if pawn.get("id") not in deposit_ids:
                continue
            carrying = pawn.get("carrying") or {}
            amount = carrying.get("amount", 0)
            resource = _normalize_resource_type(carrying.get("resource"))
            if resource in ("gold", "wood", "meat") and isinstance(amount, (int, float)):
                stocks[resource] = stocks.get(resource, 0) + amount

    if team_state.get("unit_count", 0) >= rules.get("max_units", 40):
        return []
    castle = get_my_castle(state, team)
    if castle is None:
        return []

    meat_cost = team_state.get("next_meat_cost", 0)
    if stocks.get("meat", 0) < meat_cost:
        return []

    preferred = choose_recruitment_type(state, team, rules)
    if preferred is None:
        return []      # composition complète : rien à recruter
    alive = _living_team_units(state, team)
    warriors = sum(1 for u in alive if u.get("type", u.get("unit_type")) == "warrior")
    lancers = sum(1 for u in alive if u.get("type", u.get("unit_type")) == "lancer")
    pawns = sum(1 for u in alive if u.get("type", u.get("unit_type")) == "pawn")

    # Jamais de guerrier : si l'unité voulue n'est pas abordable, on attend
    # (la répartition des pions enverra davantage de monde sur l'or).
    candidates = [preferred]

    # Dédupliquer et essayer dans l'ordre stratégique.
    for unit_type in dict.fromkeys(candidates):
        gold_cost = _get_gold_cost(rules, unit_type)
        if stocks.get("gold", 0) < gold_cost:
            continue
        return [{"type": "recruit", "building": castle["id"], "unit_type": unit_type}]

    return []


# ============================================================
# RÉPARATION
# ============================================================

def get_repair_actions(
    state,
    team,
    rules
):
    """
    Demande une réparation si :

        - le château est endommagé
        - il y a du bois disponible

    Le serveur consomme tout le bois disponible
    lors d'une réparation.
    """

    actions = []

    castle = get_my_castle(
        state,
        team
    )

    if castle is None:
        return actions

    stocks = get_my_resources(
        state,
        team
    )

    hp = castle.get(
        "hp",
        0
    )

    max_hp = castle.get(
        "max_hp",
        0
    )

    wood = stocks.get(
        "wood",
        0
    )

    missing_hp = max(
        0,
        max_hp - hp
    )

    # Réparation sans gaspillage : tout le bois est consommé, donc on attend que
    # les dégâts couvrent le stock, sauf urgence (château sous la moitié de ses PV).
    # Au dernier tour, on répare toujours : les dégâts de ce tour sont subis avant la
    # réparation, et les gardes déposent leur bois juste avant (départage aux PV).
    max_turns = int(rules.get("max_turns", 500) or 500)
    turn = int(state.get("turn", 0) or 0)
    if turn >= max_turns:
        return [{"type": "repair", "building": castle["id"]}]
    urgent = hp < max_hp * 0.5
    if missing_hp > 0 and wood > 0 and (wood <= missing_hp or urgent):

        actions.append(
            {
                "type": "repair",
                "building": castle["id"]
            }
        )

    return actions