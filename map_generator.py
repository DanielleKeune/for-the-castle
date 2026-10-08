"""Generation de la carte (portage Python de l'ancien game_manager.gd).

La carte est une grande ile en symetrie centrale : le miroir d'une case (x, y)
est la case (W - 1 - x, H - 1 - y). Toutes les ecritures passent par
_set_sym() (ou ecrivent explicitement les deux cotes, cf. les chateaux) pour
que les deux moities restent strictement equivalentes.
Le chateau de chaque camp est le seul batiment du jeu.

Usage autonome, pour visualiser une carte dans la console :
    py map_generator.py 42
"""

from __future__ import annotations

import random
import sys
from enum import IntEnum


class Terrain(IntEnum):
    """Meme ordre que l'enum Terrain de enums.gd : les codes doivent coller."""
    WATER = 0
    GROUND = 1
    ROCK = 2
    GOLD = 3
    TREE = 4
    SHEEP = 5
    CASTLE_PURPLE = 6
    CASTLE_YELLOW = 7


MARGIN = 15            # Anneau d'eau purement visuel autour de l'ile.
GROUND_MIN = 40
GROUND_MAX = 50
COAST_MAX_DEPTH = 6    # Profondeur max grignotee sur le littoral.
CASTLE_W = 4
CASTLE_H = 2
CASTLE_INSET = 10      # Distance entre le chateau et le bord de l'ile.
CASTLE_CLEARANCE = 3   # Anneau de terrain degage autour d'un chateau.

DIRECTIONS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def generate_map(seed: int | None = None) -> dict:
    """Retourne {"seed", "width", "height", "grid"} avec grid[y][x] = code Terrain.

    Une meme seed redonne toujours exactement la meme carte ; sans seed (ou
    avec une seed negative) on en tire une au hasard.
    """
    if seed is None or seed < 0:
        seed = random.randrange(2 ** 31)
    return _MapGenerator(seed).build()


class _MapGenerator:
    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.rng = random.Random(seed)
        self.width = 0
        self.height = 0
        self.grid: list[list[int]] = []

    def build(self) -> dict:
        ground_w = self.rng.randint(GROUND_MIN, GROUND_MAX)
        ground_h = self.rng.randint(GROUND_MIN, GROUND_MAX)
        self.width = ground_w + 2 * MARGIN
        self.height = ground_h + 2 * MARGIN

        self._fill_water()
        self._carve_island(ground_w, ground_h)
        self._dig_lakes(ground_w, ground_h)

        # Les cases deja occupees servent a espacer tout ce qu'on pose ensuite.
        taken = self._place_castles()
        self._place_rocks(taken)
        # Les recoins fermes disparaissent avant les ressources : ce qui reste
        # de terre est donc toujours exploitable.
        self._drop_isolated_land(taken[0])
        self._place_resources(taken)

        return {
            "seed": self.seed,
            "width": self.width,
            "height": self.height,
            "grid": [[int(cell) for cell in row] for row in self.grid],
        }

    # ------------------------------------------------------------------ relief
    def _fill_water(self) -> None:
        self.grid = [[Terrain.WATER.value] * self.width for _ in range(self.height)]

    def _carve_island(self, ground_w: int, ground_h: int) -> None:
        for y in range(MARGIN, MARGIN + ground_h):
            for x in range(MARGIN, MARGIN + ground_w):
                self.grid[y][x] = Terrain.GROUND

        # On ne dessine que le littoral nord et ouest : la symetrie centrale se
        # charge du sud et de l'est, et les coins s'arrondissent tout seuls la
        # ou les deux profils se recouvrent.
        north = self._coast_profile(ground_w)
        for i in range(ground_w):
            for d in range(north[i]):
                self._set_sym((MARGIN + i, MARGIN + d), Terrain.WATER)

        west = self._coast_profile(ground_h)
        for i in range(ground_h):
            for d in range(west[i]):
                self._set_sym((MARGIN + d, MARGIN + i), Terrain.WATER)

    def _coast_profile(self, length: int) -> list[int]:
        """Marche aleatoire douce (+/- 1 toutes les deux cases), ramenee a 0
        autour d'un point d'ancrage : l'ile touche toujours son rectangle."""
        profile = [0] * length
        depth = self.rng.randint(1, 3)
        for i in range(length):
            if i % 2 == 0:
                depth = max(0, min(COAST_MAX_DEPTH, depth + self.rng.randint(-1, 1)))
            profile[i] = depth

        anchor = self.rng.randint(COAST_MAX_DEPTH + 2, length - COAST_MAX_DEPTH - 3)
        for i in range(length):
            profile[i] = min(profile[i], abs(i - anchor))
        return profile

    def _dig_lakes(self, ground_w: int, ground_h: int) -> None:
        """Quelques petits lacs interieurs, de rayon 3 au plus pour ne jamais
        couper l'ile en deux."""
        inset = COAST_MAX_DEPTH + 6
        for _ in range(self.rng.randint(2, 4)):
            radius = self.rng.randint(2, 3)
            center = (
                self.rng.randint(MARGIN + inset, MARGIN + ground_w - inset - 1),
                self.rng.randint(MARGIN + inset, MARGIN + ground_h - inset - 1),
            )
            self._blob(center, radius, Terrain.WATER)

    def _blob(self, center: tuple[int, int], radius: int, terrain: Terrain) -> None:
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                # Le bruit ajoute au rayon evite des lacs parfaitement ronds.
                if dx * dx + dy * dy > radius * radius + self.rng.randint(0, radius):
                    continue
                self._set_sym((center[0] + dx, center[1] + dy), terrain)

    # ------------------------------------------- chateaux, ressources, rochers
    def _place_castles(self) -> list[tuple[int, int]]:
        """Pose le CASTLE_PURPLE dans le coin nord-ouest ; son miroir devient le
        CASTLE_YELLOW. Retourne les cases occupees."""
        origin = (
            MARGIN + CASTLE_INSET + self.rng.randint(0, 4),
            MARGIN + CASTLE_INSET + self.rng.randint(0, 4),
        )

        # On degage d'abord la zone : un lac ou un bout de cote ne doit pas
        # mordre sur la base de depart.
        for dy in range(-CASTLE_CLEARANCE, CASTLE_H + CASTLE_CLEARANCE):
            for dx in range(-CASTLE_CLEARANCE, CASTLE_W + CASTLE_CLEARANCE):
                self._set_sym((origin[0] + dx, origin[1] + dy), Terrain.GROUND)

        taken: list[tuple[int, int]] = []
        for dy in range(CASTLE_H):
            for dx in range(CASTLE_W):
                cell = (origin[0] + dx, origin[1] + dy)
                twin = self._mirror(cell)
                self.grid[cell[1]][cell[0]] = Terrain.CASTLE_PURPLE
                self.grid[twin[1]][twin[0]] = Terrain.CASTLE_YELLOW
                taken.append(cell)
                taken.append(twin)
        return taken

    def _place_resources(self, taken: list[tuple[int, int]]) -> None:
        """5 a 7 gisements de chaque type par moitie, soit 10 a 14 sur la carte."""
        for kind in (Terrain.GOLD, Terrain.TREE, Terrain.SHEEP):
            for _ in range(self.rng.randint(10, 20)):
                cell = self._pick_spot(taken, 3)
                if cell is None:
                    continue
                self._set_sym(cell, kind)
                taken.append(cell)
                taken.append(self._mirror(cell))

    def _place_rocks(self, taken: list[tuple[int, int]]) -> None:
        for _ in range(self.rng.randint(15, 25)):
            cell = self._pick_spot(taken, 5)
            if cell is None:
                continue
            self._set_sym(cell, Terrain.ROCK)
            taken.append(cell)
            taken.append(self._mirror(cell))

    def _drop_isolated_land(self, start: tuple[int, int]) -> None:
        """Toute terre qu'on ne peut pas rejoindre a pied depuis un chateau
        devient de l'eau. Les deux moities etant identiques, le resultat reste
        symetrique."""
        reachable = {start}
        queue = [start]
        while queue:
            x, y = queue.pop()
            for dx, dy in DIRECTIONS:
                nxt = (x + dx, y + dy)
                if not self._inside(nxt) or nxt in reachable:
                    continue
                kind = self.grid[nxt[1]][nxt[0]]
                if kind in (Terrain.WATER, Terrain.ROCK):
                    continue
                reachable.add(nxt)
                queue.append(nxt)

        for y in range(self.height):
            for x in range(self.width):
                if self.grid[y][x] == Terrain.GROUND and (x, y) not in reachable:
                    self._set_sym((x, y), Terrain.WATER)

    def _pick_spot(self, taken: list[tuple[int, int]], spacing: int) -> tuple[int, int] | None:
        """Case de GROUND libre dans la premiere moitie de la carte, a `spacing`
        cases au moins de tout ce qui est deja pose. On relache l'espacement
        plutot que d'echouer."""
        while spacing > 0:
            for _ in range(200):
                cell = (
                    self.rng.randint(0, self.width - 1),
                    self.rng.randint(0, self.height - 1),
                )
                if not self._is_first_half(cell) or not self._is_ground_pair(cell):
                    continue
                twin = self._mirror(cell)
                if all(_spread(cell, other) >= spacing and _spread(twin, other) >= spacing
                       for other in taken):
                    return cell
            spacing -= 1
        return None

    # -------------------------------------------------------------- symetrie
    def _mirror(self, cell: tuple[int, int]) -> tuple[int, int]:
        return (self.width - 1 - cell[0], self.height - 1 - cell[1])

    def _set_sym(self, cell: tuple[int, int], terrain: Terrain) -> None:
        if not self._inside(cell):
            return
        twin = self._mirror(cell)
        self.grid[cell[1]][cell[0]] = terrain
        self.grid[twin[1]][twin[0]] = terrain

    def _inside(self, cell: tuple[int, int]) -> bool:
        return 0 <= cell[0] < self.width and 0 <= cell[1] < self.height

    def _is_ground_pair(self, cell: tuple[int, int]) -> bool:
        if not self._inside(cell):
            return False
        twin = self._mirror(cell)
        return (self.grid[cell[1]][cell[0]] == Terrain.GROUND
                and self.grid[twin[1]][twin[0]] == Terrain.GROUND)

    def _is_first_half(self, cell: tuple[int, int]) -> bool:
        """La case centrale exacte (dimensions impaires) est son propre miroir :
        elle n'appartient a aucune moitie, on ne pose donc rien dessus."""
        return cell[1] * self.width + cell[0] < (self.width * self.height) // 2


def _spread(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


if __name__ == "__main__":
    SYMBOLS = "~.#GTSPY"
    result = generate_map(int(sys.argv[1]) if len(sys.argv) > 1 else None)
    print(f"seed {result['seed']}  {result['width']}x{result['height']}")
    for row in result["grid"]:
        print("".join(SYMBOLS[int(c)] for c in row))
