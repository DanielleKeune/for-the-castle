# combat.py
# IA3 v2 — défense, assaut tant que le rapport de forces est favorable (départ à x1,2, ou
# armée complète à égalité ; repli sous x1), archers et lanciers groupés, blessés rentrés.
# Gestion des déplacements de combat, des attaques et des soins.
# Ce fichier reste séparé de economy.py.

from collections import deque

# Taille de l'armée qui déclenche l'assaut, et taille à laquelle il s'arrête.
WAVE_SIZE = 16          # armée finale : elle part dès que le rapport de forces est au moins égal
RETREAT_SIZE = 2
# Assaut selon le rapport de forces : on part quand notre armée de campagne vaut
# ATTACK_RATIO fois toute l'armée adverse, on rentre sous RETREAT_RATIO.
ATTACK_RATIO = 1.2      # avantage pris : on repousse l'armée adverse jusqu'à son château
RETREAT_RATIO = 1.0     # l'assaut continue tant que nous sommes plus forts, repli sinon
FULL_ARMY_RATIO = 1.3   # armée finale réunie : on part avec un avantage (le défenseur est avantagé)
MIN_ATTACKERS = 6
# Grand choc de fin de partie (voir _combat_plan).
LATE_CLASH_TURN = 300
LATE_CLASH_UNITS = 38
LATE_CLASH_HP_RATIO = 1.3
# Les moines ne s'éloignent pas à plus de cette distance du château.
MONK_HOME_RANGE = 5
# Les archers forment une garde permanente du château, entre ces deux distances.
ARCHER_ZONE_MIN = 2
ARCHER_ZONE_MAX = 4

# Mémoire de l'assaut en cours, par camp (hystérésis entre RETREAT_SIZE et WAVE_SIZE).
_ASSAULT_ACTIVE = {}
# Assaut final « quitte ou double » engagé, par camp (aucun repli tant qu'il reste un lancier).
_ALL_IN = {}
# Archers blessés (<= ARCHER_WOUNDED_AT de leurs PV) : ils rentrent se faire soigner
# et ne repartent qu'à ARCHER_HEALED_AT.
ARCHER_WOUNDED_AT = 0.5
ARCHER_HEALED_AT = 0.9
_WOUNDED = {}
# Pendant un assaut, les lanciers ne prennent pas plus de LEASH cases d'avance sur
# les archers (sauf s'ils sont au contact), et poursuivent les ennemis à ENGAGE_RADIUS.
LEASH = 3
ENGAGE_RADIUS = 6

# Archers de contournement : recrutés à partir de FLANK_START (voir economy.py), ils
# passent chacun par un côté de la carte, attendent à FLANK_STAGING cases du château
# adverse, puis arrivent à portée au tour FLANK_STRIKE_TURN pour tirer sur le château.
FLANK_START = 300
FLANK_ARCHERS = 2
FLANK_STAGING = (10, 11)
FLANK_STRIKE_TURN = 498
_FLANK = {}              # camp -> mémoire du contournement


def _turn(rules, turn_at_500):
    """Tour équivalent à `turn_at_500` quand la partie dure rules["max_turns"] tours.

    Les réglages de fin de partie sont écrits pour une partie de 500 tours ; ils
    sont décalés pour garder la même distance avec le dernier tour.
    """
    max_turns = int((rules or {}).get("max_turns", 500) or 500)
    return max(0, turn_at_500 + max_turns - 500)

# Pillard : le premier guerrier de la partie part tuer les pions adverses.
# Il n'est désigné qu'une fois : s'il meurt, il n'est pas remplacé (les
# guerriers recrutés plus tard, faute d'or pour un lancier, restent dans l'armée).
_RAIDER = {}             # camp -> id du pillard (None s'il est mort)

# Priorité des cibles pour le tir concentré : soutiens, puis combattants, puis pions.
_TARGET_PRIORITY = {"archer": 0, "monk": 0, "warrior": 1, "lancer": 1, "pawn": 2}


def _distance(a, b):
    """Distance de Chebyshev : les diagonales comptent comme 1."""
    return max(abs(a["x"] - b["x"]), abs(a["y"] - b["y"]))


def _get_units(state):
    """Récupère la liste des unités, si elle existe."""
    units = state.get("units", [])
    if isinstance(units, dict):
        return list(units.values())
    return units if isinstance(units, list) else []


def _get_buildings(state):
    """Récupère la liste des bâtiments."""
    buildings = state.get("buildings", [])
    if isinstance(buildings, dict):
        return list(buildings.values())
    return buildings if isinstance(buildings, list) else []


def _team_of(obj):
    """Récupère le camp d'une unité ou d'un bâtiment."""
    return obj.get("team", obj.get("owner"))


def _position(obj):
    """Récupère x et y, que la position soit séparée ou dans pos."""
    if "x" in obj and "y" in obj:
        return {"x": obj["x"], "y": obj["y"]}
    pos = obj.get("pos")
    if isinstance(pos, (list, tuple)) and len(pos) >= 2:
        return {"x": pos[0], "y": pos[1]}
    return None


def _terrain_name(code, legend):
    """Convertit un code de grille en nom de terrain.

    Le protocole peut fournir rules.legend sous forme de liste
    (legend[code]) ou de dictionnaire (legend[code]).
    """
    if isinstance(legend, list):
        if isinstance(code, int) and 0 <= code < len(legend):
            return legend[code]
        if isinstance(code, str):
            return code
        return None

    if isinstance(legend, dict):
        value = legend.get(str(code), legend.get(code))
        if value is not None:
            return value

    # Sécurité si le serveur ne fournit pas de légende exploitable.
    known_codes = {
        0: "water", 1: "ground", 2: "rock", 3: "gold",
        4: "tree", 5: "sheep", 6: "castle_purple", 7: "castle_yellow",
    }
    if isinstance(code, int):
        return known_codes.get(code)
    return code if isinstance(code, str) else None


def _in_bounds(x, y, width, height):
    return 0 <= x < width and 0 <= y < height


def _building_cells(buildings, width, height):
    """Toutes les cases occupées par les bâtiments (ils bloquent le chemin)."""
    cells = set()
    for building in buildings:
        pos = _position(building)
        if pos is None:
            continue
        x0, y0 = pos["x"], pos["y"]
        bw = int(building.get("width", 1) or 1)
        bh = int(building.get("height", 1) or 1)
        for y in range(y0, y0 + bh):
            for x in range(x0, x0 + bw):
                if _in_bounds(x, y, width, height):
                    cells.add((x, y))
    return cells


def _chebyshev_to_target(x, y, target):
    """Distance à une unité ou à n'importe quelle case d'un bâtiment."""
    pos = _position(target)
    if pos is None:
        return 10**9

    if "width" not in target and "height" not in target:
        return max(abs(x - pos["x"]), abs(y - pos["y"]))

    x0, y0 = pos["x"], pos["y"]
    x1 = x0 + max(1, int(target.get("width", 1) or 1)) - 1
    y1 = y0 + max(1, int(target.get("height", 1) or 1)) - 1
    dx = max(x0 - x, 0, x - x1)
    dy = max(y0 - y, 0, y - y1)
    return max(dx, dy)


def _reachable_from_unit(start, move_range, grid, walkable_codes, building_cells,
                         enemy_cells, occupied_cells, width, height):
    """Cases atteignables en move_range pas, avec chemins en 4 directions."""
    queue = deque([(start[0], start[1])])
    distances = {(start[0], start[1]): 0}
    directions = ((1, 0), (-1, 0), (0, 1), (0, -1))

    while queue:
        x, y = queue.popleft()
        steps = distances[(x, y)]
        if steps >= move_range:
            continue

        for dx, dy in directions:
            nx, ny = x + dx, y + dy
            point = (nx, ny)
            if not _in_bounds(nx, ny, width, height) or point in distances:
                continue
            if _terrain_name(grid[ny][nx], walkable_codes[1]) not in walkable_codes[0]:
                continue
            if point in building_cells or point in enemy_cells:
                continue
            distances[point] = steps + 1
            queue.append(point)

    # Une unité peut traverser ses alliés, mais ne peut pas finir sur une case occupée.
    return {
        point: steps for point, steps in distances.items()
        if point not in occupied_cells and point != (start[0], start[1])
    }, distances


def _distance_map_from_goals(goals, grid, walkable_names, legend, building_cells,
                             enemy_cells, width, height):
    """Distances de chemin jusqu'à une case où l'unité peut attaquer sa cible."""
    queue = deque()
    distances = {}
    for point in goals:
        if point not in distances:
            distances[point] = 0
            queue.append(point)

    directions = ((1, 0), (-1, 0), (0, 1), (0, -1))
    while queue:
        x, y = queue.popleft()
        next_distance = distances[(x, y)] + 1
        for dx, dy in directions:
            nx, ny = x + dx, y + dy
            point = (nx, ny)
            if not _in_bounds(nx, ny, width, height) or point in distances:
                continue
            if _terrain_name(grid[ny][nx], legend) not in walkable_names:
                continue
            if point in building_cells or point in enemy_cells:
                continue
            distances[point] = next_distance
            queue.append(point)
    return distances


def _unit_stats(unit, rules):
    kind = unit.get("type", unit.get("unit_type"))
    return rules.get("units", {}).get(kind, {})


def _is_attack_fighter(unit, rules):
    kind = unit.get("type", unit.get("unit_type"))
    return kind != "pawn" and _unit_stats(unit, rules).get("attack", unit.get("attack", 0)) > 0


def _is_healer(unit, rules):
    return _unit_stats(unit, rules).get("heal", unit.get("heal", 0)) > 0


def _unit_label(unit):
    kind = unit.get("type", unit.get("unit_type", "unité"))
    names = {
        "pawn": "Pion", "warrior": "Guerrier", "lancer": "Lancier",
        "archer": "Archer", "monk": "Moine"
    }
    return f"{names.get(kind, str(kind).capitalize())} {unit.get('id', '?')}"


def _combat_plan(state, team, rules):
    """Retourne les unités et la répartition entre garde et attaque.

    Envoyer les combattants un par un vers le château adverse les fait mourir
    sans résultat. Tant que l'armée compte moins de WAVE_SIZE combattants,
    tout le monde défend notre château (là où le robot perd ses vagues et où
    nos recrues apparaissent). À WAVE_SIZE, toute l'armée part en une seule
    vague, et elle ne rentre que quand il reste RETREAT_SIZE combattants ou moins.
    """
    units = _get_units(state)
    buildings = _get_buildings(state)
    alive = [u for u in units if u.get("hp", 0) > 0 and _position(u) is not None]
    allies = [u for u in alive if _team_of(u) == team]
    enemies = [u for u in alive if _team_of(u) not in (None, team)]
    own_castles = [b for b in buildings if _team_of(b) == team and b.get("hp", 0) > 0
                   and _position(b) is not None and b.get("type", "castle") == "castle"]
    enemy_castles = [b for b in buildings if _team_of(b) not in (None, team) and b.get("hp", 0) > 0
                     and _position(b) is not None and b.get("type", "castle") == "castle"]
    own_castle = own_castles[0] if own_castles else None

    armed = [u for u in allies if _is_attack_fighter(u, rules)]
    # Les archers (lents et fragiles) restent toujours au château : ils ne
    # comptent pas dans la vague d'assaut.
    flank_ids = _update_flankers(state, team, allies, rules)
    archer_ids = {u.get("id") for u in armed if u.get("type") == "archer" and u.get("id") not in flank_ids}
    raider_id = _update_raider(team, allies)
    field_army = [u for u in armed if u.get("id") not in archer_ids and u.get("id") != raider_id]

    def castle_distance(unit):
        if own_castle is None:
            return 10**9
        p = _position(unit)
        return _chebyshev_to_target(p["x"], p["y"], own_castle) if p else 10**9

    threatening_enemies = [e for e in enemies if _is_attack_fighter(e, rules)
                           and castle_distance(e) <= 10]
    close_threat = any(castle_distance(e) <= 6 for e in threatening_enemies)
    castle_ratio = 1.0
    if own_castle is not None:
        castle_ratio = own_castle.get("hp", 0) / max(1, own_castle.get("max_hp", 1))
    emergency = close_threat or castle_ratio <= 0.50

    # Décision d'assaut selon le rapport de forces (loi de Lanchester :
    # force d'un groupe = somme des PV x somme des dégâts). On compare notre
    # armée de campagne à TOUTE l'armée adverse, garde du château comprise.
    count = len(field_army)
    wounded_now = _WOUNDED.get(team, set())
    fit_archers = [u for u in armed if u.get("id") in archer_ids and u.get("id") not in wounded_now]
    our_strength = _group_strength(field_army + fit_archers, rules)
    their_strength = _group_strength([e for e in enemies if _is_attack_fighter(e, rules)], rules)
    assault = _ASSAULT_ACTIVE.get(team, False)
    if not assault:
        # Départ : avantage net (ATTACK_RATIO), ou armée finale réunie et rapport au moins égal.
        favourable = our_strength >= ATTACK_RATIO * their_strength
        full_and_even = count >= WAVE_SIZE and our_strength >= FULL_ARMY_RATIO * their_strength
        # Armée adverse chez elle : >= 80 % de ses combattants à 6 cases de son château.
        enemy_f = [e for e in enemies if _is_attack_fighter(e, rules)]
        home = [e for e in enemy_f if enemy_castles and _chebyshev_to_target(
            _position(e)["x"], _position(e)["y"], enemy_castles[0]) <= 6]
        at_home = bool(enemy_f) and len(home) >= 0.8 * len(enemy_f)
        # Grand choc de fin de partie : après LATE_CLASH_TURN, contre une armée adverse
        # au complet (au moins LATE_CLASH_UNITS unités) et chez elle, il faut aussi
        # LATE_CLASH_HP_RATIO fois ses PV réels. Les assauts plus tôt ne changent pas.
        our_hp = sum(u.get("hp", 0) for u in field_army + fit_archers)
        their_hp = sum(e.get("hp", 0) for e in enemy_f)
        late_clash = (int(state.get("turn", 0) or 0) >= _turn(rules, LATE_CLASH_TURN) and at_home
                      and len(enemies) >= LATE_CLASH_UNITS)
        if late_clash and our_hp < LATE_CLASH_HP_RATIO * their_hp:
            favourable = full_and_even = False
        if count >= MIN_ATTACKERS and (favourable or full_and_even) and not close_threat:
            assault = True
    elif count <= RETREAT_SIZE or our_strength < RETREAT_RATIO * their_strength:
        # On continue tant que le rapport de forces reste favorable ; sinon repli.
        assault = False
    _ASSAULT_ACTIVE[team] = assault

    # Archers blessés : ils rentrent ; les autres accompagnent l'assaut.
    wounded = _WOUNDED.setdefault(team, set())
    wounded &= archer_ids
    for u in armed:
        if u.get("id") not in archer_ids:
            continue
        ratio = u.get("hp", 0) / max(1, u.get("max_hp", 1))
        if ratio <= ARCHER_WOUNDED_AT:
            wounded.add(u.get("id"))
        elif ratio >= ARCHER_HEALED_AT:
            wounded.discard(u.get("id"))
    # Quand la vague part, tous les archers la suivent (blessés compris) : seuls les
    # pions et les archers de contournement ne partent pas.
    assault_archers = set(archer_ids) if assault else set()

    if own_castle is None or assault:
        # Assaut lancé : toute l'armée part ensemble.
        guard_ids = set()
    else:
        # Armée en formation : tout le monde reste groupé en défense.
        guard_ids = {u.get("id") for u in field_army}

    return {
        "flank_ids": flank_ids,
        "assault": assault,
        "assault_archers": assault_archers,
        "wounded_archers": set(wounded),
        "raider_id": raider_id,
        "archer_ids": archer_ids,
        "allies": allies,
        "enemies": enemies,
        "own_castle": own_castle,
        "enemy_castles": enemy_castles,
        "guard_ids": guard_ids,
        "threatening_enemies": threatening_enemies,
        "emergency": emergency,
    }


def _update_flankers(state, team, allies, rules=None):
    """Archers de contournement : les FLANK_ARCHERS premiers archers recrutés après FLANK_START."""
    turn = int(state.get("turn", 0) or 0)
    if turn < _turn(rules, FLANK_START):
        return set()
    memo = _FLANK.get(team)
    if memo is None:
        memo = {"min_id": max((u.get("id", 0) for u in allies), default=0), "ids": [],
                "side": {}, "reached": set(), "cache": {}}
        _FLANK[team] = memo
    alive = {u.get("id") for u in allies}
    for u in sorted(allies, key=lambda u: u.get("id", 0)):
        if len(memo["side"]) >= FLANK_ARCHERS:
            break
        if u.get("type") == "archer" and u.get("id", 0) > memo["min_id"] and u.get("id") not in memo["side"]:
            memo["side"][u.get("id")] = 1 if len(memo["side"]) % 2 == 0 else -1
    return {uid for uid in memo["side"] if uid in alive}


def _flank_destination(unit, state, rules, team, own_castle, enemy_castle, occupied_cells,
                       enemy_cells, building_cells, reserved_destinations):
    """Contournement : point de passage sur le côté, attente hors de vue, frappe finale."""
    memo = _FLANK[team]
    grid = state.get("grid")
    height, width = len(grid), len(grid[0])
    legend = rules.get("legend", {})
    walkable_names = set(rules.get("walkable", ["ground", "tree", "sheep"]))
    side = memo["side"][unit["id"]]
    pos = _position(unit)
    start = (pos["x"], pos["y"])
    turn = int(state.get("turn", 0) or 0)
    move_range = int(_unit_stats(unit, rules).get("move", 1) or 1)
    attack_range = int(_unit_stats(unit, rules).get("range", 4) or 4)

    def walkable(x, y):
        return 0 <= x < width and 0 <= y < height and _terrain_name(grid[y][x], legend) in walkable_names

    cache = memo["cache"].setdefault(side, {})
    ocx = own_castle["x"] + own_castle["width"] / 2.0
    ocy = own_castle["y"] + own_castle["height"] / 2.0
    ecx = enemy_castle["x"] + enemy_castle["width"] / 2.0
    ecy = enemy_castle["y"] + enemy_castle["height"] / 2.0
    dx, dy = ecx - ocx, ecy - ocy
    norm = max(1e-6, (dx * dx + dy * dy) ** 0.5)
    px, py = -dy / norm * side, dx / norm * side          # perpendiculaire, côté choisi

    if "waypoint" not in cache:
        home = _distance_map_from_goals({start}, grid, walkable_names, legend, building_cells,
                                        set(), width, height)
        mx, my = (ocx + ecx) / 2.0, (ocy + ecy) / 2.0
        waypoint = None
        for k in range(30, 3, -1):
            cx, cy = int(round(mx + px * k)), int(round(my + py * k))
            near = [(cx + a, cy + b) for a in range(-2, 3) for b in range(-2, 3)
                    if walkable(cx + a, cy + b) and (cx + a, cy + b) in home]
            if near:
                waypoint = min(near, key=lambda c: abs(c[0] - cx) + abs(c[1] - cy))
                break
        cache["waypoint"] = waypoint
        # Cases d'attente (hors de vue) et de tir, du côté choisi.
        def on_side(c):
            return (c[0] - ecx) * px + (c[1] - ecy) * py >= 0
        staging = {(x, y) for y in range(height) for x in range(width)
                   if walkable(x, y) and on_side((x, y))
                   and FLANK_STAGING[0] <= _chebyshev_to_target(x, y, enemy_castle) <= FLANK_STAGING[1]}
        firing = {(x, y) for y in range(height) for x in range(width)
                  if walkable(x, y) and on_side((x, y))
                  and 2 <= _chebyshev_to_target(x, y, enemy_castle) <= attack_range}
        if waypoint is not None and staging:
            from_wp = _distance_map_from_goals({waypoint}, grid, walkable_names, legend,
                                               building_cells, set(), width, height)
            staging = set(sorted(staging, key=lambda c: from_wp.get(c, 10 ** 6))[:8])
        cache["staging"] = _distance_map_from_goals(staging, grid, walkable_names, legend,
                                                    building_cells, set(), width, height)
        cache["firing"] = _distance_map_from_goals(firing, grid, walkable_names, legend,
                                                   building_cells, set(), width, height)
        cache["wp_map"] = (_distance_map_from_goals({waypoint}, grid, walkable_names, legend,
                                                    building_cells, set(), width, height)
                           if waypoint else {})

    waypoint = cache["waypoint"]
    if waypoint is not None and unit["id"] not in memo["reached"]:
        if max(abs(start[0] - waypoint[0]), abs(start[1] - waypoint[1])) <= 2:
            memo["reached"].add(unit["id"])
    if waypoint is not None and unit["id"] not in memo["reached"]:
        goal_map, why = cache["wp_map"], "contourne la carte"
    else:
        steps_to_fire = cache["firing"].get(start, 10 ** 6) / max(1, move_range)
        if turn + steps_to_fire >= _turn(rules, FLANK_STRIKE_TURN):
            goal_map, why = cache["firing"], "frappe finale sur le château"
        else:
            goal_map, why = cache["staging"], "attend hors de vue"
    here = goal_map.get(start)
    if here == 0:
        return None, why, None
    reachable, _ = _reachable_from_unit(start, move_range, grid, (walkable_names, legend),
                                        building_cells, enemy_cells, occupied_cells, width, height)
    options = [(goal_map[p], -st, p) for p, st in reachable.items()
               if p in goal_map and p not in reserved_destinations
               and (here is None or goal_map[p] < here)]
    if not options and here is not None:
        # Pas de côté pour contourner un obstacle (souvent un pion qui dépose) :
        # une case libre à distance égale, ou au pire une case plus loin.
        for slack in (0, 1):
            options = [(goal_map[p], -st, p) for p, st in reachable.items()
                       if p in goal_map and p not in reserved_destinations
                       and goal_map[p] <= here + slack and p != start]
            if options:
                options.sort()
                return options[0][2], why + " (contourne un obstacle)", None
    if not options:
        # Bloqué : la case suivante du chemin est tenue par un allié, qui devra s'écarter.
        nexts = [(goal_map[n], n) for n in ((start[0] + 1, start[1]), (start[0] - 1, start[1]),
                                            (start[0], start[1] + 1), (start[0], start[1] - 1))
                 if n in goal_map and n in occupied_cells and n not in enemy_cells
                 and (here is None or goal_map[n] < here)]
        return None, why + " (bloqué, un allié s'écarte)", (min(nexts)[1] if nexts else None)
    options.sort()
    return options[0][2], why, None


def _group_strength(units, rules):
    """Force d'un groupe au combat (loi de Lanchester) : somme des PV x somme des dégâts."""
    hp = sum(u.get("hp", 0) for u in units)
    damage = sum(_unit_stats(u, rules).get("attack", 0) for u in units)
    return hp * damage


def _update_raider(team, allies):
    """Désigne le pillard (premier guerrier vu) et l'oublie quand il meurt."""
    alive_ids = {u.get("id") for u in allies}
    if team not in _RAIDER:
        warriors = sorted((u for u in allies if u.get("type") == "warrior"), key=lambda u: u.get("id", 0))
        if warriors:
            _RAIDER[team] = warriors[0].get("id")
        return _RAIDER.get(team)
    if _RAIDER[team] not in alive_ids:
        _RAIDER[team] = None
    return _RAIDER[team]


def _raider_destination(unit, state, rules, enemies, occupied_cells,
                        enemy_cells, building_cells, reserved_destinations):
    """Déplacement du pillard : fuir les combattants, sinon chasser les pions."""
    grid = state.get("grid")
    height, width = len(grid), len(grid[0])
    legend = rules.get("legend", {})
    walkable_names = set(rules.get("walkable", ["ground", "tree", "sheep"]))
    stats = _unit_stats(unit, rules)
    move_range = int(stats.get("move", 3) or 3)
    attack_range = int(stats.get("range", 1) or 1)
    pos = _position(unit)
    start = (pos["x"], pos["y"])

    # Zone de danger : ce qu'un combattant ennemi peut frapper au prochain tour.
    fighters = []
    for e in enemies:
        if not _is_attack_fighter(e, rules):
            continue
        es = _unit_stats(e, rules)
        p = _position(e)
        fighters.append((p["x"], p["y"], int(es.get("move", 0) or 0) + int(es.get("range", 0) or 0)))

    def danger(x, y):
        """Marge avant d'être à portée d'un combattant (0 ou moins = menacé)."""
        if not fighters:
            return 99
        return min(max(abs(x - fx), abs(y - fy)) - reach for fx, fy, reach in fighters)

    reachable, _ = _reachable_from_unit(
        start, move_range, grid, (walkable_names, legend), building_cells,
        enemy_cells, occupied_cells, width, height
    )
    reachable = {p: st for p, st in reachable.items() if p not in reserved_destinations}

    if danger(*start) <= 0:
        safer = [(danger(*p), -st, p) for p, st in reachable.items() if danger(*p) > danger(*start)]
        if safer:
            safer.sort(reverse=True)
            return safer[0][2], "fuit les combattants ennemis"
        return None, "encerclé, reste sur place"

    # Proies : les pions ennemis hors de portée des combattants adverses d'abord.
    pawns = [e for e in enemies if e.get("type") == "pawn"]
    pawns.sort(key=lambda e: (0 if danger(_position(e)["x"], _position(e)["y"]) > 0 else 1,
                              _distance(pos, _position(e))))
    for prey in pawns:
        pp = _position(prey)
        if _distance(pos, pp) <= attack_range:
            return None, "frappe le pion %s" % prey.get("id")
        goals = {(pp["x"] + dx, pp["y"] + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                 if (dx or dy) and _in_bounds(pp["x"] + dx, pp["y"] + dy, width, height)}
        dmap = _distance_map_from_goals(goals, grid, walkable_names, legend, building_cells,
                                        enemy_cells, width, height)
        if start not in dmap:
            continue
        options = [(dmap[p], -st, p) for p, st in reachable.items()
                   if p in dmap and dmap[p] < dmap[start] and danger(*p) > 0]
        if options:
            options.sort()
            return options[0][2], "chasse le pion %s" % prey.get("id")
    return None, "aucune proie atteignable sans danger"


def _path_step_toward_target(unit, target, attack_range, move_range, state, rules,
                              occupied_cells, enemy_cells, building_cells,
                              reserved_destinations):
    """Renvoie une destination atteignable qui rapproche de la portée d'attaque."""
    grid = state.get("grid")
    if not isinstance(grid, list) or not grid or not grid[0]:
        return None
    height, width = len(grid), len(grid[0])
    pos = _position(unit)
    target_pos = _position(target)
    if pos is None or target_pos is None:
        return None
    start = (pos["x"], pos["y"])
    if _chebyshev_to_target(start[0], start[1], target) <= attack_range:
        return None

    legend = rules.get("legend", {})
    walkable_names = set(rules.get("walkable", ["ground", "tree", "sheep"]))
    if "width" in target or "height" in target:
        tx0, ty0 = target_pos["x"], target_pos["y"]
        tw = max(1, int(target.get("width", 1) or 1))
        th = max(1, int(target.get("height", 1) or 1))
    else:
        tx0, ty0, tw, th = target_pos["x"], target_pos["y"], 1, 1

    x0 = max(0, tx0 - attack_range - 1)
    x1 = min(width - 1, tx0 + tw + attack_range)
    y0 = max(0, ty0 - attack_range - 1)
    y1 = min(height - 1, ty0 + th + attack_range)
    goals = set()
    for gy in range(y0, y1 + 1):
        for gx in range(x0, x1 + 1):
            point = (gx, gy)
            if point in occupied_cells or point in building_cells or point in reserved_destinations:
                continue
            if not _in_bounds(gx, gy, width, height):
                continue
            if _terrain_name(grid[gy][gx], legend) not in walkable_names:
                continue
            if _chebyshev_to_target(gx, gy, target) <= attack_range:
                goals.add(point)

    if not goals:
        return None
    distances = _distance_map_from_goals(
        goals, grid, walkable_names, legend, building_cells, enemy_cells, width, height
    )
    current_distance = distances.get(start)
    if current_distance is None or current_distance <= 0:
        return None
    reachable, _ = _reachable_from_unit(
        start, max(1, move_range), grid, (walkable_names, legend), building_cells,
        enemy_cells, occupied_cells, width, height
    )
    options = []
    for point, steps in reachable.items():
        if point in reserved_destinations:
            continue
        remaining = distances.get(point)
        if remaining is not None and remaining < current_distance:
            # Choisir le maximum de progression sans finir sur une case occupée.
            options.append((remaining, -steps, point))
    if not options:
        return None
    options.sort()
    return options[0][2]


# Phase finale : à partir de FINAL_START, chaque combattant de FINAL_KINDS part au
# dernier moment (selon son trajet et sa vitesse) pour arriver à portée du château
# adverse au tour FINAL_ARRIVAL ; une fois à portée, il ne frappe plus que le château.
# Les pertes n'ont plus d'importance : seuls comptent les PV des châteaux au tour 500.
FINAL_START = 400
FINAL_ARRIVAL = 494
FINAL_MARGIN = 1.2
FINAL_KINDS = ("lancer", "warrior")
_FINAL_STRIKE = {}       # camp -> {"ids": unités parties, "maps": cartes de distance}


def _final_map(memo, reach, castle, grid, walkable_names, legend, building_cells, width, height):
    key = (reach, castle.get("id"))
    if key not in memo["maps"]:
        goals = {(x, y) for y in range(height) for x in range(width)
                 if (x, y) not in building_cells
                 and _terrain_name(grid[y][x], legend) in walkable_names
                 and 1 <= _chebyshev_to_target(x, y, castle) <= reach}
        memo["maps"][key] = _distance_map_from_goals(goals, grid, walkable_names, legend,
                                                     building_cells, set(), width, height)
    return memo["maps"][key]


def _final_reach(unit, rules):
    if unit.get("type") == "monk":
        return 3                 # le moine reste juste derrière, à portée de soin
    return max(1, int(_unit_stats(unit, rules).get("range", 1) or 1))


def _final_strikers(state, team, rules, allies, plan, castle, grid, walkable_names, legend,
                    building_cells, width, height):
    """Met à jour et renvoie (ids partis, mémoire) de la phase finale."""
    memo = _FINAL_STRIKE.setdefault(team, {"ids": set(), "maps": {}})
    memo["ids"] &= {u.get("id") for u in allies}
    if int(state.get("turn", 0) or 0) < _turn(rules, FINAL_START) or castle is None:
        return memo["ids"], memo
    turn = int(state.get("turn", 0) or 0)       # phase de déplacement : tour précédent
    for u in allies:
        uid = u.get("id")
        if (uid in memo["ids"] or u.get("type") not in FINAL_KINDS
                or uid in plan["flank_ids"] or uid == plan["raider_id"]):
            continue
        dmap = _final_map(memo, _final_reach(u, rules), castle, grid, walkable_names, legend,
                          building_cells, width, height)
        p = _position(u)
        steps = dmap.get((p["x"], p["y"]))
        if steps is None:
            continue
        move = max(1, int(_unit_stats(u, rules).get("move", 1) or 1))
        if turn + 1 + int(-(-FINAL_MARGIN * steps // move)) >= _turn(rules, FINAL_ARRIVAL):
            memo["ids"].add(uid)
    return memo["ids"], memo


def get_combat_moves(state, team, rules):
    """Répartit la majorité des combattants vers l'attaque et une minorité en garde.

    Les pions économiques restent entièrement gérés par economy.py.
    """
    grid = state.get("grid")
    if not isinstance(grid, list) or not grid or not isinstance(grid[0], list):
        return []
    height, width = len(grid), len(grid[0])
    if width == 0:
        return []

    plan = _combat_plan(state, team, rules)
    allies = plan["allies"]
    enemies = plan["enemies"]
    own_castle = plan["own_castle"]
    enemy_castles = plan["enemy_castles"]
    guard_ids = plan["guard_ids"]
    archer_ids = plan["archer_ids"]
    raider_id = plan["raider_id"]
    threatening_enemies = plan["threatening_enemies"]
    assault_archers = plan["assault_archers"]
    wounded_archers = plan["wounded_archers"]
    unit_rules = rules.get("units", {})
    legend = rules.get("legend", {})
    walkable_names = set(rules.get("walkable", ["ground", "tree", "sheep"]))

    alive_units = [u for u in _get_units(state) if u.get("hp", 0) > 0 and _position(u) is not None]
    occupied_cells = set()
    enemy_cells = set()
    for u in alive_units:
        p = _position(u)
        point = (p["x"], p["y"])
        occupied_cells.add(point)
        if _team_of(u) not in (None, team):
            enemy_cells.add(point)
    building_cells = _building_cells(_get_buildings(state), width, height)

    def walkable(x, y):
        return (0 <= x < width and 0 <= y < height
                and _terrain_name(grid[y][x], legend) in walkable_names)

    def castle_distance_xy(x, y):
        if own_castle is None:
            return 10**9
        return _chebyshev_to_target(x, y, own_castle)

    armed = [u for u in allies if _is_attack_fighter(u, rules)]
    healers = [u for u in allies if _is_healer(u, rules) and not _is_attack_fighter(u, rules)]
    # Traiter les défenseurs d'abord, puis le groupe offensif, puis les soigneurs.
    ordered = sorted([u for u in armed if u.get("id") in guard_ids],
                     key=lambda u: (castle_distance_xy(_position(u)["x"], _position(u)["y"]), u.get("id", 0)))
    # Pendant l'assaut, les archers bougent avant les lanciers (le groupe se règle sur eux).
    ordered += sorted([u for u in armed if u.get("id") not in guard_ids],
                      key=lambda u: (0 if u.get("id") in plan["assault_archers"] else 1, u.get("id", 0)))
    ordered += sorted(healers, key=lambda u: u.get("id", 0))

    # Case-cibles de la garde : couronne défensive libre autour du château.
    guard_goals = set()
    if own_castle is not None:
        for gy in range(height):
            for gx in range(width):
                point = (gx, gy)
                if not 2 <= _chebyshev_to_target(gx, gy, own_castle) <= 5:
                    continue
                if not walkable(gx, gy) or point in building_cells or point in occupied_cells:
                    continue
                guard_goals.add(point)

    # Laisse des lanciers : distance (en pas) au château adverse de l'archer le plus avancé.
    enemy_fighters = [e for e in enemies if _is_attack_fighter(e, rules)]
    front = None
    to_enemy_castle = {}
    if assault_archers and enemy_castles:
        castle = enemy_castles[0]
        goals = {(gx, gy) for gy in range(castle["y"] - 4, castle["y"] + castle["height"] + 4)
                 for gx in range(castle["x"] - 4, castle["x"] + castle["width"] + 4)
                 if walkable(gx, gy) and _chebyshev_to_target(gx, gy, castle) <= 4}
        to_enemy_castle = _distance_map_from_goals(goals, grid, walkable_names, legend,
                                                   building_cells, set(), width, height)
        archer_steps = [to_enemy_castle.get((_position(a)["x"], _position(a)["y"]))
                        for a in allies if a.get("id") in assault_archers]
        archer_steps = [d for d in archer_steps if d is not None]
        if archer_steps:
            front = min(archer_steps)

    moves = []
    reserved_destinations = set()
    archers_stuck = False

    # Archers de contournement : décision avant les autres. S'ils sont coincés dans la
    # couronne de garde, l'allié posté sur leur chemin s'écarte d'une case.
    flank_plan = {}
    make_way = {}
    if plan["flank_ids"] and own_castle is not None and enemy_castles:
        ally_at = {(_position(a)["x"], _position(a)["y"]): a for a in allies}
        for u in allies:
            if u.get("id") not in plan["flank_ids"]:
                continue
            destination, why, blocked = _flank_destination(
                u, state, rules, team, own_castle, enemy_castles[0], occupied_cells,
                enemy_cells, building_cells, reserved_destinations
            )
            flank_plan[u["id"]] = (destination, why)
            if destination is not None:
                reserved_destinations.add(destination)
            elif blocked in ally_at and ally_at[blocked].get("id") not in plan["flank_ids"]:
                make_way[ally_at[blocked]["id"]] = blocked

    final_ids, final_memo = _final_strikers(
        state, team, rules, allies, plan, enemy_castles[0] if enemy_castles else None,
        grid, walkable_names, legend, building_cells, width, height)

    for unit in ordered:
        unit_id = unit.get("id")
        kind = unit.get("type", unit.get("unit_type"))
        stats = unit_rules.get(kind, {})
        attack_power = stats.get("attack", unit.get("attack", 0))
        heal_power = stats.get("heal", unit.get("heal", 0))
        attack_range = int(stats.get("range", unit.get("range", 0)) or 0)
        move_range = int(stats.get("move", unit.get("move", 0)) or 0)
        pos = _position(unit)
        if pos is None or move_range <= 0:
            continue
        start = (pos["x"], pos["y"])

        # Phase finale : droit sur une case d'où frapper le château adverse.
        if unit_id in final_ids and enemy_castles:
            dmap = _final_map(final_memo, _final_reach(unit, rules), enemy_castles[0], grid,
                              walkable_names, legend, building_cells, width, height)
            here = dmap.get(start)
            if here != 0:
                reach, _ = _reachable_from_unit(start, move_range, grid, (walkable_names, legend),
                                                building_cells, enemy_cells, occupied_cells, width, height)
                options = [(dmap[p], -st, p) for p, st in reach.items()
                           if p in dmap and p not in reserved_destinations
                           and (here is None or dmap[p] < here)]
                if options:
                    options.sort()
                    destination = options[0][2]
                    moves.append({"unit": unit_id, "to": [destination[0], destination[1]]})
                    reserved_destinations.add(destination)
            print(f"[COMBAT] {_unit_label(unit)} : phase finale, en route vers le château adverse.", flush=True)
            continue

        # Allié qui bloque un archer de contournement : il s'écarte d'une case.
        if unit_id in make_way:
            reach, _ = _reachable_from_unit(start, move_range, grid, (walkable_names, legend),
                                            building_cells, enemy_cells, occupied_cells, width, height)
            spots = sorted((st, p) for p, st in reach.items() if p not in reserved_destinations)
            if spots:
                destination = spots[0][1]
                moves.append({"unit": unit_id, "to": [destination[0], destination[1]]})
                reserved_destinations.add(destination)
                print(f"[COMBAT] {_unit_label(unit)} : s'écarte pour laisser passer un archer de contournement.", flush=True)
                continue

        # Pillard : il harcèle l'économie adverse en tuant les pions isolés.
        if unit_id == raider_id:
            destination, why = _raider_destination(
                unit, state, rules, enemies, occupied_cells,
                enemy_cells, building_cells, reserved_destinations
            )
            if destination is not None:
                moves.append({"unit": unit_id, "to": [destination[0], destination[1]]})
                reserved_destinations.add(destination)
            print(f"[COMBAT] {_unit_label(unit)} (pillard) : {why}.", flush=True)
            continue

        # Archers de contournement : ils passent par un côté de la carte et
        # frappent le château adverse aux derniers tours.
        if unit_id in flank_plan:
            destination, why = flank_plan[unit_id]
            if destination is not None:
                moves.append({"unit": unit_id, "to": [destination[0], destination[1]]})
                reserved_destinations.add(destination)
            print(f"[COMBAT] {_unit_label(unit)} (contournement) : {why}.", flush=True)
            continue

        # Archers d'assaut : ils accompagnent les lanciers, tirent sur les combattants
        # ennemis, et sur le château quand l'armée adverse a reculé.
        if unit_id in assault_archers:
            if any(_distance(pos, _position(e)) <= attack_range for e in enemy_fighters):
                print(f"[COMBAT] {_unit_label(unit)} : tire sur l'armée adverse.", flush=True)
                continue
            close = [e for e in enemy_fighters if _distance(pos, _position(e)) <= ENGAGE_RADIUS + attack_range]
            if close:
                target = min(close, key=lambda e: _distance(pos, _position(e)))
            elif enemy_castles:
                target = enemy_castles[0]
            else:
                continue
            if _chebyshev_to_target(start[0], start[1], target) <= attack_range:
                continue
            destination = _path_step_toward_target(
                unit, target, attack_range, move_range, state, rules,
                occupied_cells, enemy_cells, building_cells, reserved_destinations
            )
            if destination is not None:
                moves.append({"unit": unit_id, "to": [destination[0], destination[1]]})
                reserved_destinations.add(destination)
                print(f"[COMBAT] {_unit_label(unit)} : archer d'assaut en marche -> {destination}.", flush=True)
            else:
                # Bloqué (souvent par nos propres lanciers) : ce tour-ci, les lanciers
                # avancent sans attendre pour libérer le passage.
                archers_stuck = True
                print(f"[COMBAT] {_unit_label(unit)} : archer d'assaut bloqué, les lanciers ouvrent la voie.", flush=True)
            continue

        # Archers de garde (ou blessés qui rentrent) : ils restent entre ARCHER_ZONE_MIN
        # et ARCHER_ZONE_MAX cases du château, à côté d'un moine s'il y en a un.
        if unit_id in archer_ids and own_castle is not None:
            close_fighters = [e for e in enemy_fighters if _distance(pos, _position(e)) <= 2]
            if close_fighters:
                # Ennemi au contact : reculer d'une case en restant à portée de tir.
                reach, _ = _reachable_from_unit(start, move_range, grid, (walkable_names, legend),
                                                building_cells, enemy_cells, occupied_cells, width, height)
                best = None
                for p in reach:
                    if p in reserved_destinations or p == start:
                        continue
                    dmin = min(max(abs(p[0] - _position(e)["x"]), abs(p[1] - _position(e)["y"]))
                               for e in enemy_fighters)
                    if dmin <= 2 or dmin > attack_range:
                        continue
                    key = (dmin, -castle_distance_xy(p[0], p[1]))
                    if best is None or key > best[0]:
                        best = (key, p)
                if best is not None:
                    moves.append({"unit": unit_id, "to": [best[1][0], best[1][1]]})
                    reserved_destinations.add(best[1])
                    print(f"[COMBAT] {_unit_label(unit)} : recule derrière les lanciers.", flush=True)
                    continue
            if unit_id not in wounded_archers and any(
                    _distance(pos, _position(e)) <= attack_range for e in enemies):
                print(f"[COMBAT] {_unit_label(unit)} : tient sa position, un ennemi est à portée de tir.", flush=True)
                continue

            def in_zone(x, y):
                return ARCHER_ZONE_MIN <= castle_distance_xy(x, y) <= ARCHER_ZONE_MAX

            zone = [(gx, gy) for (gx, gy) in guard_goals if in_zone(gx, gy)]
            if in_zone(*start):
                zone.append(start)
            goals = zone
            if threatening_enemies:
                # Se placer pour tirer sur un intrus, toujours dans la zone de garde.
                shooting = [c for c in goals if any(
                    max(abs(c[0] - _position(e)["x"]), abs(c[1] - _position(e)["y"])) <= attack_range
                    for e in threatening_enemies)]
                goals = shooting or goals
            else:
                monks = [a for a in allies if _is_healer(a, rules) and not _is_attack_fighter(a, rules)]
                if monks:
                    heal_reach = max(int(_unit_stats(m, rules).get("range", 2) or 2) for m in monks)
                    near_monk = [c for c in goals if any(
                        max(abs(c[0] - _position(m)["x"]), abs(c[1] - _position(m)["y"])) <= heal_reach
                        for m in monks)]
                    goals = near_monk or goals
            goal_set = {c for c in goals if c not in reserved_destinations}
            if start in goal_set or not goal_set:
                print(f"[COMBAT] {_unit_label(unit)} : garde le château.", flush=True)
                continue
            distance_map = _distance_map_from_goals(
                goal_set, grid, walkable_names, legend, building_cells, enemy_cells, width, height
            )
            current = distance_map.get(start)
            reachable, _ = _reachable_from_unit(
                start, move_range, grid, (walkable_names, legend), building_cells,
                enemy_cells, occupied_cells, width, height
            )
            options = [(distance_map[p], -steps, p) for p, steps in reachable.items()
                       if p not in reserved_destinations and p in distance_map
                       and (current is None or distance_map[p] < current)]
            if options:
                options.sort()
                destination = options[0][2]
                moves.append({"unit": unit_id, "to": [destination[0], destination[1]]})
                reserved_destinations.add(destination)
                print(f"[COMBAT] {_unit_label(unit)} : se place en garde près du château -> {destination}.", flush=True)
            continue

        # Les moines se positionnent à portée des alliés blessés, sans attaquer.
        if heal_power > 0 and attack_power <= 0:
            injured = [a for a in allies if a.get("hp", 0) < a.get("max_hp", a.get("hp", 0))]
            if plan["assault"]:
                # Pendant l'assaut, le moine part avec la vague : il ne soigne que ses combattants.
                injured = [a for a in injured if _is_attack_fighter(a, rules)
                           and a.get("id") not in plan["flank_ids"]]
            injured.sort(key=lambda a: (
                0 if a.get("type") in ("warrior", "lancer", "archer") else 1,
                a.get("hp", 0) / max(1, a.get("max_hp", 1)),
                _chebyshev_to_target(start[0], start[1], a),
            ))
            target = injured[0] if injured else None
            if target is None and plan["assault"]:
                # Personne à soigner : suivre le combattant de la vague le plus proche.
                wave = [a for a in allies if _is_attack_fighter(a, rules)
                        and a.get("id") not in plan["flank_ids"] and a.get("id") != raider_id]
                if wave:
                    target = min(wave, key=lambda a: _distance(pos, _position(a)))
            if target is None:
                print(f"[COMBAT] {_unit_label(unit)} : reste en soutien, aucun allié blessé à soigner pour l'instant.", flush=True)
                continue
            if _distance(pos, _position(target)) <= attack_range:
                print(f"[COMBAT] {_unit_label(unit)} : reste en soutien près de l'allié blessé {_unit_label(target)}.", flush=True)
                continue
            destination = _path_step_toward_target(
                unit, target, attack_range, move_range, state, rules,
                occupied_cells, enemy_cells, building_cells, reserved_destinations
            )
            # Le moine reste près du château : il soigne les blessés qui rentrent
            # au lieu de suivre la vague (il est trop lent et c'est une cible prioritaire).
            if (destination is not None and own_castle is not None and not plan["assault"]
                    and castle_distance_xy(destination[0], destination[1]) > MONK_HOME_RANGE):
                print(f"[COMBAT] {_unit_label(unit)} : reste près du château, {_unit_label(target)} est trop loin.", flush=True)
                continue
            if destination is not None:
                moves.append({"unit": unit_id, "to": [destination[0], destination[1]]})
                reserved_destinations.add(destination)
                print(f"[COMBAT] {_unit_label(unit)} : avance pour se mettre à portée de soin de {_unit_label(target)} -> {destination}.", flush=True)
            else:
                print(f"[COMBAT] {_unit_label(unit)} : ne peut pas rejoindre la portée de soin de {_unit_label(target)} ce tour.", flush=True)
            continue

        if attack_power <= 0:
            continue

        is_guard = unit_id in guard_ids
        if is_guard and own_castle is not None:
            if threatening_enemies:
                targets = sorted(threatening_enemies, key=lambda e: (
                    _chebyshev_to_target(_position(e)["x"], _position(e)["y"], own_castle),
                    e.get("hp", 999999),
                    _distance(pos, _position(e)),
                ))
                target = targets[0]
                if _distance(pos, _position(target)) <= attack_range:
                    print(f"[COMBAT] {_unit_label(unit)} : reste en interception, l'ennemi {_unit_label(target)} est à portée.", flush=True)
                    continue
                destination = _path_step_toward_target(
                    unit, target, attack_range, move_range, state, rules,
                    occupied_cells, enemy_cells, building_cells, reserved_destinations
                )
                if destination is not None:
                    moves.append({"unit": unit_id, "to": [destination[0], destination[1]]})
                    reserved_destinations.add(destination)
                    print(f"[COMBAT] {_unit_label(unit)} : intercepte {_unit_label(target)} pour défendre le château -> {destination}.", flush=True)
                else:
                    print(f"[COMBAT] {_unit_label(unit)} : garde le château; chemin d'interception indisponible ce tour.", flush=True)
                continue

            current_distance = _chebyshev_to_target(start[0], start[1], own_castle)
            if 2 <= current_distance <= 5:
                print(f"[COMBAT] {_unit_label(unit)} : reste autour du château pour le défendre.", flush=True)
                continue
            if guard_goals:
                distance_map = _distance_map_from_goals(
                    guard_goals, grid, walkable_names, legend, building_cells,
                    enemy_cells, width, height
                )
                current_distance = distance_map.get(start)
                reachable, _ = _reachable_from_unit(
                    start, move_range, grid, (walkable_names, legend), building_cells,
                    enemy_cells, occupied_cells, width, height
                )
                options = []
                if current_distance is not None:
                    for point, steps in reachable.items():
                        if point in reserved_destinations:
                            continue
                        remaining = distance_map.get(point)
                        if remaining is not None and remaining < current_distance:
                            options.append((remaining, -steps, point))
                if options:
                    options.sort()
                    destination = options[0][2]
                    moves.append({"unit": unit_id, "to": [destination[0], destination[1]]})
                    reserved_destinations.add(destination)
                    print(f"[COMBAT] {_unit_label(unit)} : se repositionne dans la zone de garde -> {destination}.", flush=True)
                else:
                    print(f"[COMBAT] {_unit_label(unit)} : reste en défense; aucun déplacement sûr disponible.", flush=True)
            else:
                print(f"[COMBAT] {_unit_label(unit)} : reste près du château pour le défendre.", flush=True)
            continue

        # Groupe offensif : objectif principal = château adverse. Ne pas disperser
        # la vague pour poursuivre des ennemis près de leur base; la phase d'actions
        # attaquera le château dès qu'il sera à portée. Les soldats ennemis ne sont
        # ciblés en déplacement que si le château n'est pas disponible.
        # Poursuite : les combattants ennemis proches d'abord (on repousse leur armée),
        # sinon le château adverse.
        target = None
        pursuing = False
        near_enemies = [e for e in enemy_fighters if _distance(pos, _position(e)) <= ENGAGE_RADIUS]
        if near_enemies:
            target = min(near_enemies, key=lambda e: (_distance(pos, _position(e)), e.get("hp", 0)))
            pursuing = True
        elif enemy_castles:
            enemy_castles.sort(key=lambda b: _chebyshev_to_target(start[0], start[1], b))
            target = enemy_castles[0]
        elif enemies:
            enemies_sorted = sorted(enemies, key=lambda e: (
                0 if _is_attack_fighter(e, rules) else 1,
                _distance(pos, _position(e)), e.get("hp", 999999)
            ))
            target = enemies_sorted[0]

        if target is None:
            print(f"[COMBAT] {_unit_label(unit)} : pas de château adverse ou d'ennemi détecté; reste prêt à défendre.", flush=True)
            continue
        destination = _path_step_toward_target(
            unit, target, attack_range, move_range, state, rules,
            occupied_cells, enemy_cells, building_cells, reserved_destinations
        )
        # Groupe : sans ennemi au contact, ne pas distancer les archers de plus de LEASH pas.
        if (destination is not None and not pursuing and front is not None and not archers_stuck
                and to_enemy_castle.get(destination, 10 ** 6) < front - LEASH):
            print(f"[COMBAT] {_unit_label(unit)} : attend les archers.", flush=True)
            continue
        if destination is not None:
            moves.append({"unit": unit_id, "to": [destination[0], destination[1]]})
            reserved_destinations.add(destination)
            if target in enemy_castles:
                print(f"[COMBAT] {_unit_label(unit)} : groupe offensif en marche vers le château adverse -> {destination}.", flush=True)
            else:
                print(f"[COMBAT] {_unit_label(unit)} : avance contre {_unit_label(target)} sans abandonner l'objectif offensif -> {destination}.", flush=True)
        else:
            if _chebyshev_to_target(start[0], start[1], target) <= attack_range:
                print(f"[COMBAT] {_unit_label(unit)} : à portée de la cible; l'action d'attaque sera évaluée.", flush=True)
            else:
                print(f"[COMBAT] {_unit_label(unit)} : n'avance pas ce tour, chemin vers la cible bloqué ou indisponible.", flush=True)

    print(f"[COMBAT] Répartition : {len(guard_ids)} combattant(s) en garde, {max(0, len(armed) - len(guard_ids))} dans le groupe offensif.", flush=True)
    return moves


def get_combat_actions(state, team, rules):
    """Génère les soins et attaques avec des messages lisibles de combat."""
    actions = []
    used_units = set()
    plan = _combat_plan(state, team, rules)
    allies = plan["allies"]
    enemies = plan["enemies"]
    own_castle = plan["own_castle"]
    enemy_castles = plan["enemy_castles"]
    guard_ids = plan["guard_ids"]
    threatening_enemies = plan["threatening_enemies"]
    unit_rules = rules.get("units", {})

    def castle_distance(candidate):
        if own_castle is None:
            return 10**9
        p = _position(candidate)
        if p is None:
            return 10**9
        return _chebyshev_to_target(p["x"], p["y"], own_castle)

    # 1. Les moines soignent l'allié militaire le plus urgent à portée; ils
    # peuvent aussi se soigner eux-mêmes quand ils sont blessés.
    for healer in allies:
        stats = _unit_stats(healer, rules)
        heal_power = stats.get("heal", healer.get("heal", 0))
        heal_range = stats.get("range", healer.get("range", 0))
        if heal_power <= 0:
            continue
        hp_pos = _position(healer)
        candidates = []
        for ally in allies:
            max_hp = ally.get("max_hp", ally.get("hp", 0))
            hp = ally.get("hp", 0)
            missing = max_hp - hp
            if missing <= 0:
                continue
            ap = _position(ally)
            if ap is None or hp_pos is None or _distance(hp_pos, ap) > heal_range:
                continue
            kind = ally.get("type", ally.get("unit_type"))
            combat_priority = 0 if kind in ("warrior", "lancer", "archer") else 1
            ratio = hp / max(1, max_hp)
            candidates.append((combat_priority, ratio, -missing, _distance(hp_pos, ap), ally))
        if candidates:
            candidates.sort(key=lambda x: x[:4])
            target = candidates[0][4]
            actions.append({"type": "heal", "unit": healer["id"], "target": target["id"]})
            used_units.add(healer["id"])
            print(f"[COMBAT] {_unit_label(healer)} : soin envoyé sur {_unit_label(target)} (jusqu'à {heal_power} PV).", flush=True)
        else:
            injured_anywhere = any(a.get("hp", 0) < a.get("max_hp", a.get("hp", 0)) for a in allies)
            reason = "aucun allié blessé à portée" if injured_anywhere else "aucun allié blessé"
            print(f"[COMBAT] {_unit_label(healer)} : ne soigne pas ce tour, {reason}.", flush=True)

    # 2. Les guerriers/lanciers/archers attaquent les cibles à portée.
    planned_damage = {}
    for attacker in allies:
        unit_id = attacker.get("id")
        if unit_id in used_units:
            continue
        kind = attacker.get("type", attacker.get("unit_type"))
        if kind == "pawn":
            continue
        stats = unit_rules.get(kind, {})
        attack_power = stats.get("attack", attacker.get("attack", 0))
        attack_range = stats.get("range", attacker.get("range", 0))
        if attack_power <= 0:
            continue
        pos = _position(attacker)
        if pos is None:
            continue

        # Tir concentré : on tient compte des dégâts déjà prévus ce tour par nos
        # autres unités. Une cible déjà condamnée est ignorée (dégâts gaspillés),
        # et on préfère une cible qu'on peut achever avec ce coup.
        target = None
        target_is_castle = False
        candidates = []
        for e in enemies:
            if _distance(pos, _position(e)) > attack_range:
                continue
            remaining = e.get("hp", 0) - planned_damage.get(e.get("id"), 0)
            if remaining <= 0:
                continue
            can_kill = 0 if remaining <= attack_power else 1
            priority = _TARGET_PRIORITY.get(e.get("type"), 3)
            if unit_id == plan["raider_id"] and e.get("type") == "pawn":
                priority = -1      # le pillard vise les pions avant tout
            candidates.append((priority, can_kill, remaining, e.get("id", 0), e))
        castles_in_range = [b for b in enemy_castles
                            if _chebyshev_to_target(pos["x"], pos["y"], b) <= attack_range]
        if unit_id in plan["flank_ids"]:
            candidates = []                # contournement : uniquement le château
        if unit_id in plan["wounded_archers"]:
            castles_in_range = []          # archer blessé : plus de tir sur le château
        if unit_id in plan["assault_archers"]:
            # Archer d'assaut : l'armée adverse d'abord ; si elle a reculé, le château.
            fighters_in_range = [c for c in candidates if _is_attack_fighter(c[4], rules)]
            if not fighters_in_range and castles_in_range:
                candidates = []
            else:
                candidates = fighters_in_range or candidates
        if unit_id in _FINAL_STRIKE.get(team, {}).get("ids", ()):
            # Phase finale : le château adverse avant tout dès qu'il est à portée.
            castles_in_range = [b for b in enemy_castles
                                if _chebyshev_to_target(pos["x"], pos["y"], b) <= attack_range]
            if castles_in_range:
                candidates = []
        if candidates:
            candidates.sort(key=lambda c: c[:4])
            target = candidates[0][4]
        elif castles_in_range:
            # Aucune unité ennemie à portée : frapper le château adverse.
            target = castles_in_range[0]
            target_is_castle = True
        if target is not None:
            planned_damage[target["id"]] = planned_damage.get(target["id"], 0) + attack_power

        if target is not None:
            if target_is_castle or target.get("type") == "castle":
                actions.append({"type": "attack", "unit": unit_id, "target": target["id"]})
                print(f"[COMBAT] {_unit_label(attacker)} : attaque le château adverse ({target['hp']} PV restants avant résolution).", flush=True)
            else:
                actions.append({"type": "attack", "unit": unit_id, "target": target["id"]})
                if castle_distance(target) <= 10 and _is_attack_fighter(target, rules):
                    why = "pour défendre notre château"
                else:
                    why = "sur le front"
                print(f"[COMBAT] {_unit_label(attacker)} : attaque {_unit_label(target)} {why}.", flush=True)
            used_units.add(unit_id)
        else:
            if unit_id in guard_ids:
                print(f"[COMBAT] {_unit_label(attacker)} : n'attaque pas ce tour; il reste en défense, aucune cible ennemie à portée.", flush=True)
            else:
                print(f"[COMBAT] {_unit_label(attacker)} : n'attaque pas ce tour; aucune cible à portée, il poursuit l'offensive vers le château adverse.", flush=True)

    return actions
