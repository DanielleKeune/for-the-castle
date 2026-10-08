"""Serveur de parties.

Le serveur ne connait PAS les regles du jeu : il genere la carte, met en
relation le moteur (Godot) et les deux IA, et cadence les tours. C'est le
moteur qui applique les ordres et renvoie l'etat ; le serveur se contente de
le faire suivre. Tout le protocole est decrit dans PROTOCOL.md.

Transport : TCP, un objet JSON par ligne (UTF-8, termine par "\\n").

Usage :
    py server.py [--host 0.0.0.0] [--port 5555] [--timeout 5] [--max-turns 500]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import string
import sys
from pathlib import Path

from map_generator import generate_map

TEAMS = ("purple", "yellow")
# Le robot se lance soit depuis son binaire livre a cote du serveur, soit
# depuis les sources (seul ce dernier cas demande le dossier clients/).
ROBOT_BINARY = Path(__file__).resolve().parent / ("robot.exe" if os.name == "nt" else "robot")
ROBOT_SCRIPT = Path(__file__).resolve().parent.parent / "clients" / "robot_bot.py"
ID_ALPHABET = string.ascii_uppercase + string.digits
LINE_LIMIT = 16 * 1024 * 1024  # Les etats complets depassent la limite par defaut (64 Ko).

log = logging.getLogger("server")


class Disconnected(Exception):
    def __init__(self, conn: "Connection") -> None:
        super().__init__(conn.label)
        self.conn = conn


class Connection:
    """Une socket cliente : envoi de messages et file des messages recus."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self.inbox: asyncio.Queue[dict | None] = asyncio.Queue()
        self.closed = False
        self.role = ""   # "" tant que la connexion n'a ni cree ni rejoint de partie
        self.team = ""
        self.name = ""
        self.game: Game | None = None
        peer = writer.get_extra_info("peername")
        self.label = f"{peer[0]}:{peer[1]}" if peer else "?"

    async def send(self, message: dict) -> None:
        if self.closed:
            return
        try:
            self.writer.write((json.dumps(message, separators=(",", ":")) + "\n").encode())
            await self.writer.drain()
        except (ConnectionError, OSError):
            self.closed = True

    async def error(self, text: str) -> None:
        await self.send({"type": "error", "message": text})

    async def recv(self, msg_type: str, turn: int | None = None,
                   timeout: float | None = None) -> dict | None:
        """Attend le prochain message `msg_type` (du tour `turn` si precise).

        Les autres messages sont ignores : typiquement une reponse arrivee
        apres le delai du tour precedent. Retourne None si le delai expire,
        leve Disconnected si la connexion tombe.
        """
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
        while True:
            if self.closed and self.inbox.empty():
                raise Disconnected(self)
            remaining = None if deadline is None else deadline - loop.time()
            if remaining is not None and remaining <= 0:
                return None
            try:
                message = await asyncio.wait_for(self.inbox.get(), remaining)
            except asyncio.TimeoutError:
                return None
            if message is None:
                raise Disconnected(self)
            if message.get("type") != msg_type:
                await self.error(f"message '{message.get('type')}' inattendu, "
                                 f"le serveur attend '{msg_type}'")
                continue
            if turn is not None and message.get("turn") != turn:
                await self.error(f"'{msg_type}' du tour {message.get('turn')} ignore, "
                                 f"tour en cours : {turn}")
                continue
            return message

    def close(self) -> None:
        self.closed = True
        self.writer.close()


class Game:
    def __init__(self, server: "Server", game_id: str, engine: Connection,
                 seed: int | None, max_turns: int) -> None:
        self.server = server
        self.id = game_id
        self.engine = engine
        self.players: list[Connection | None] = [None, None]
        self.full = asyncio.Event()
        self.started = False
        self.map = generate_map(seed)
        self.max_turns = max_turns
        self.task: asyncio.Task | None = None

    @property
    def seed(self) -> int:
        return self.map["seed"]

    # ----------------------------------------------------------------- lobby
    async def add_player(self, conn: Connection, name: str, team: str | None = None) -> None:
        if self.started or None not in self.players:
            await conn.error(f"la partie {self.id} est complete")
            return
        # Equipe demandee si elle est libre, sinon la premiere place libre.
        wanted = TEAMS.index(team) if team in TEAMS else -1
        slot = wanted if wanted >= 0 and self.players[wanted] is None else self.players.index(None)
        conn.role, conn.team, conn.name, conn.game = "player", TEAMS[slot], name, self
        self.players[slot] = conn
        log.info("[%s] %s rejoint en %s (%s)", self.id, name, conn.team, conn.label)
        await conn.send({"type": "joined", "game_id": self.id, "team": conn.team, "name": name})
        await self.engine.send({"type": "player_joined", "team": conn.team, "name": name})
        if None not in self.players:
            self.full.set()

    async def on_disconnect(self, conn: Connection) -> None:
        if self.server.games.get(self.id) is not self:
            return  # Partie deja terminee : plus rien a prevenir.
        if conn is self.engine:
            log.warning("[%s] moteur deconnecte : partie annulee", self.id)
            if self.task:
                self.task.cancel()
            await self._broadcast_end(None, "engine_disconnected", None, include_engine=False)
            self.server.games.pop(self.id, None)
        elif not self.started and conn in self.players:
            # Avant le debut, on libere simplement la place.
            self.players[self.players.index(conn)] = None
            log.info("[%s] %s quitte le lobby", self.id, conn.name)
            await self.engine.send({"type": "player_left", "team": conn.team, "name": conn.name})
        # Pendant la partie, la deconnexion est detectee par la boucle de jeu.

    # -------------------------------------------------------------- partie
    async def run(self) -> None:
        try:
            ready = await self.engine.recv("ready")
            rules, state = ready.get("rules", {}), ready.get("state", {})
            await self.full.wait()
            self.started = True
            await self._play(rules, state)
        except asyncio.CancelledError:
            pass
        except Disconnected as exc:
            if exc.conn is self.engine:
                return  # deja traite par on_disconnect
            loser = exc.conn.team
            winner = TEAMS[1 - TEAMS.index(loser)]
            log.warning("[%s] %s deconnecte : victoire de %s", self.id, exc.conn.name, winner)
            await self._broadcast_end(winner, "opponent_disconnected", None)
        finally:
            self.server.games.pop(self.id, None)

    async def _play(self, rules: dict, state: dict) -> None:
        names = {p.team: p.name for p in self.players}
        log.info("[%s] debut : %s contre %s", self.id, names["purple"], names["yellow"])
        await self.engine.send({"type": "game_start", "players": names})
        for p in self.players:
            await p.send({
                "type": "game_start", "game_id": self.id, "you": p.team,
                "opponent": names[TEAMS[1 - TEAMS.index(p.team)]],
                "rules": rules, "state": state,
            })

        events: list = []
        rejected: dict = {}
        turn = 1
        while True:
            # Phase de deplacement.
            orders = await self._collect("new_turn", "moves", turn, state, events, rejected)
            await self.engine.send({"type": "moves", "turn": turn, "orders": orders})
            done = await self.engine.recv("done", turn)
            state, events, rejected = done["state"], done.get("events", []), done.get("rejected", {})

            # Phase d'action.
            orders = await self._collect("action", "actions", turn, state, events, rejected)
            await self.engine.send({"type": "actions", "turn": turn, "orders": orders})
            done = await self.engine.recv("action_done", turn)
            state, events, rejected = done["state"], done.get("events", []), done.get("rejected", {})

            if state.get("winner"):
                log.info("[%s] fin au tour %d : %s (%s)", self.id, turn,
                         state["winner"], state.get("end_reason"))
                await self._broadcast_end(state["winner"], state.get("end_reason", ""), state,
                                          include_engine=False, events=events, rejected=rejected)
                return
            turn += 1

    async def _collect(self, ask: str, answer: str, turn: int, state: dict,
                       events: list, rejected: dict) -> dict:
        """Envoie `ask` aux deux IA puis attend leurs ordres en parallele."""
        for p in self.players:
            await p.send({
                "type": ask, "turn": turn, "you": p.team, "state": state,
                "events": events, "rejected": rejected.get(p.team, []),
            })
        results = await asyncio.gather(*(self._orders_of(p, answer, turn) for p in self.players))
        return dict(zip(TEAMS, results))

    async def _orders_of(self, player: Connection, answer: str, turn: int) -> list:
        message = await player.recv(answer, turn, self.server.timeout)
        if message is None:
            log.info("[%s] tour %d : %s n'a pas repondu a temps", self.id, turn, player.name)
            await player.error(f"delai depasse pour '{answer}' au tour {turn} : aucun ordre retenu")
            return []
        orders = message.get(answer, [])
        if not isinstance(orders, list):
            await player.error(f"'{answer}' doit etre une liste")
            return []
        return orders

    async def _broadcast_end(self, winner: str | None, reason: str, state: dict | None,
                             include_engine: bool = True, events: list | None = None,
                             rejected: dict | None = None) -> None:
        message = {"type": "game_over", "winner": winner, "reason": reason, "state": state}
        for p in self.players:
            if p is not None:
                await p.send({**message, "events": events or [],
                              "rejected": (rejected or {}).get(p.team, [])})
                p.close()
        if include_engine:
            await self.engine.send(message)


class Server:
    def __init__(self, port: int, timeout: float, max_turns: int) -> None:
        self.port = port
        self.timeout = timeout
        self.max_turns = max_turns
        self.games: dict[str, Game] = {}
        self._robots: set[asyncio.Task] = set()   # Processus robots en cours.

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn = Connection(reader, writer)
        log.debug("connexion de %s", conn.label)
        try:
            while True:
                try:
                    line = await reader.readline()
                except (ConnectionError, OSError, asyncio.LimitOverrunError, ValueError):
                    break
                if not line:
                    break
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        raise ValueError
                except ValueError:
                    await conn.error("chaque ligne doit etre un objet JSON")
                    continue
                if conn.role:
                    conn.inbox.put_nowait(message)
                else:
                    await self._lobby(conn, message)
        finally:
            conn.closed = True
            conn.inbox.put_nowait(None)
            if conn.game is not None:
                await conn.game.on_disconnect(conn)
            writer.close()

    async def _lobby(self, conn: Connection, message: dict) -> None:
        kind = message.get("type")
        if kind == "create_game":
            seed = message.get("seed")
            max_turns = message.get("max_turns") or self.max_turns
            opponent = message.get("opponent", "none")
            if not _is_int_or_none(seed) or not isinstance(max_turns, int):
                await conn.error("'seed' et 'max_turns' doivent etre des entiers")
                return
            if opponent not in ("none", "robot"):
                await conn.error("'opponent' doit valoir 'none' ou 'robot'")
                return
            game = Game(self, self._new_id(), conn, seed, max_turns)
            conn.role, conn.game = "engine", game
            self.games[game.id] = game
            log.info("partie %s creee (seed %d, %dx%d%s)", game.id, game.seed,
                     game.map["width"], game.map["height"],
                     ", contre le robot" if opponent == "robot" else "")
            await conn.send({
                "type": "game_created", "game_id": game.id, "seed": game.seed,
                "max_turns": max_turns, "opponent": opponent, "map": game.map,
            })
            game.task = asyncio.create_task(game.run())
            if opponent == "robot":
                await self._launch_robot(game)
        elif kind == "join":
            game_id = str(message.get("game_id", "")).strip().upper()
            game = self.games.get(game_id)
            if game is None:
                await conn.error(f"aucune partie '{game_id}'")
                return
            name = str(message.get("name") or "anonyme")[:32]
            await game.add_player(conn, name, message.get("team"))
        else:
            await conn.error("commencez par 'create_game' ou 'join'")

    async def _launch_robot(self, game: Game) -> None:
        """Le robot est une IA comme les autres : un processus a part qui
        rejoint la partie en yellow, la place purple restant a l'utilisateur."""
        if ROBOT_BINARY.exists():
            command = [str(ROBOT_BINARY)]
        elif ROBOT_SCRIPT.exists():
            command = [sys.executable, str(ROBOT_SCRIPT)]
        else:
            log.error("robot introuvable (%s ni %s) : la partie %s reste sans "
                      "adversaire", ROBOT_BINARY.name, ROBOT_SCRIPT.name, game.id)
            return
        process = await asyncio.create_subprocess_exec(
            *command, game.id, "--team", "yellow",
            "--port", str(self.port), "--quiet",
            stdout=asyncio.subprocess.DEVNULL)
        self._robots.add(asyncio.create_task(process.wait()))
        self._robots = {task for task in self._robots if not task.done()}

    def _new_id(self) -> str:
        while True:
            game_id = "".join(random.choices(ID_ALPHABET, k=6))
            if game_id not in self.games:
                return game_id


def _is_int_or_none(value: object) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool))


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5555)
    parser.add_argument("--timeout", type=float, default=5.0,
                        help="secondes laissees a chaque IA pour repondre (defaut 5)")
    parser.add_argument("--max-turns", type=int, default=500,
                        help="nombre de tours max si la partie n'en precise pas (defaut 500)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    server = Server(args.port, args.timeout, args.max_turns)
    tcp = await asyncio.start_server(server.handle, args.host, args.port, limit=LINE_LIMIT)
    log.info("serveur en ecoute sur %s:%d", args.host, args.port)
    async with tcp:
        await tcp.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
