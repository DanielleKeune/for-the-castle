# TEMPLATE IA3 v2 — 14 pions, 8 archers, 2 moines, 16 lanciers, assaut selon le rapport de forces, réparation du château
import argparse
import json
import socket
import traceback

from economy import (
    get_economy_moves,
    get_economy_actions,
    get_recruitment_actions,
    get_repair_actions,
    choose_recruitment_type,
)

from combat import get_combat_moves, get_combat_actions


def play(game_id, name, host, port, wanted_team=None):
    print(f"Connexion au serveur {host}:{port}...", flush=True)
    try:
        sock = socket.create_connection((host, port), timeout=10)
    except OSError as exc:
        print(f"ERREUR : impossible de joindre le serveur : {exc}", flush=True)
        return
    # Le délai de 10 s ne sert qu'à la connexion : ensuite on attend les messages
    # sans limite (attente de l'adversaire, animations du moteur entre les tours).
    sock.settimeout(None)

    with sock:
        stream = sock.makefile("rw", encoding="utf-8", newline="\n")

        def send(message):
            stream.write(json.dumps(message) + "\n")
            stream.flush()

        def safe_call(function, *arguments):
            try:
                orders = function(*arguments)
                return orders if isinstance(orders, list) else []
            except Exception:
                traceback.print_exc()
                return []

        join = {"type": "join", "game_id": game_id, "name": name}
        if wanted_team:
            join["team"] = wanted_team
        send(join)

        team = None
        rules = None

        for line in stream:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                print("Message JSON invalide reçu :", line.rstrip(), flush=True)
                continue

            msg_type = message.get("type")
            if msg_type == "game_start":
                team = message["you"]
                rules = message["rules"]
                print("Partie commencée ! Équipe :", team, flush=True)

            elif msg_type == "new_turn":
                if team is None or rules is None:
                    print("new_turn reçu avant game_start : ignoré.", flush=True)
                    continue

                turn = message["turn"]
                state = message["state"]
                if message.get("rejected"):
                    print("Ordres refusés au tour précédent :", message["rejected"], flush=True)
                economy_moves = safe_call(get_economy_moves, state, team, rules)
                combat_moves = safe_call(get_combat_moves, state, team, rules)

                # Fusionner sans donner deux destinations à la même unité.
                moves = []
                used_units = set()
                used_destinations = set()
                for move in economy_moves + combat_moves:
                    unit_id = move.get("unit")
                    destination = move.get("to")
                    if unit_id is None or unit_id in used_units:
                        continue
                    if not isinstance(destination, (list, tuple)) or len(destination) != 2:
                        continue
                    destination_key = (destination[0], destination[1])
                    # Deux unités ne doivent pas recevoir la même case d'arrivée.
                    if destination_key in used_destinations:
                        continue
                    moves.append(move)
                    used_units.add(unit_id)
                    used_destinations.add(destination_key)

                send({"type": "moves", "turn": turn, "moves": moves})
                print(f"Tour {turn} - déplacements économiques :", economy_moves, flush=True)
                print(f"Tour {turn} - déplacements combat :", combat_moves, flush=True)
                print(f"Tour {turn} - déplacements envoyés :", moves, flush=True)

            elif msg_type == "action":
                if team is None or rules is None:
                    print("action reçu avant game_start : ignoré.", flush=True)
                    continue

                turn = message["turn"]
                state = message["state"]
                if message.get("events"):
                    print("Événements après déplacement :", message["events"], flush=True)
                if message.get("rejected"):
                    print("Déplacements refusés par le serveur :", message["rejected"], flush=True)
                combat_actions = safe_call(get_combat_actions, state, team, rules)
                economy_actions = safe_call(get_economy_actions, state, team, rules)
                recruitment_actions = safe_call(
                    get_recruitment_actions,
                    state,
                    team,
                    rules,
                    economy_actions
                )
                repair_actions = safe_call(get_repair_actions, state, team, rules)

                # Diagnostic utile : explique pourquoi aucun recrutement n'a
                # été proposé (souvent la viande manque parce que son coût monte).
                if not recruitment_actions:
                    team_state = state.get("teams", {}).get(team, {})
                    stocks = team_state.get("resources", {})
                    alive = [u for u in state.get("units", [])
                             if u.get("team") == team and u.get("hp", 0) > 0]
                    pawn_count = sum(1 for u in alive if u.get("type") == "pawn")
                    warrior_count = sum(1 for u in alive if u.get("type") == "warrior")
                    lancer_count = sum(1 for u in alive if u.get("type") == "lancer")
                    monk_count = sum(1 for u in alive if u.get("type") == "monk")
                    meat_cost = team_state.get("next_meat_cost", 0)
                    preferred = safe_call(choose_recruitment_type, state, team, rules)
                    print(
                        f"Recrutement en attente - viande {stocks.get('meat', 0)}/{meat_cost}, "
                        f"or {stocks.get('gold', 0)} (pion 1 / guerrier 5 / lancier 10 / moine 15), "
                        f"pions {pawn_count}, guerriers {warrior_count}, lanciers {lancer_count}, "
                        f"moines {monk_count}, priorité {preferred}",
                        flush=True,
                    )

                actions = []
                used_units = set()
                all_actions = combat_actions + economy_actions + recruitment_actions + repair_actions
                for action in all_actions:
                    action_type = action.get("type")
                    if action_type in ("recruit", "repair"):
                        actions.append(action)
                        continue
                    unit_id = action.get("unit")
                    if unit_id is None or unit_id in used_units:
                        continue
                    actions.append(action)
                    used_units.add(unit_id)

                send({"type": "actions", "turn": turn, "actions": actions})
                print(f"Tour {turn} - combat :", combat_actions, flush=True)
                print(f"Tour {turn} - économie :", economy_actions, flush=True)
                print(f"Tour {turn} - recrutement :", recruitment_actions, flush=True)
                print(f"Tour {turn} - réparation :", repair_actions, flush=True)
                print(f"Tour {turn} - actions envoyées :", actions, flush=True)

            elif msg_type == "error":
                print("Erreur du serveur :", message, flush=True)
            elif msg_type in ("game_over", "game_end"):
                print("Fin de partie :", message, flush=True)
                break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="IA économique et combat")
    parser.add_argument("game_id", help="ID de la partie affiché dans la fenêtre du jeu")
    parser.add_argument("--name", default="my-ai")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5555)
    parser.add_argument("--team", choices=("purple", "yellow"), default=None,
                        help="Équipe souhaitée")
    args = parser.parse_args()
    play(args.game_id, args.name, args.host, args.port, args.team)
