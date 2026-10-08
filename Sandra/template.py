import argparse
import json
import socket
import traceback


def play(game_id, name, host, port, wanted_team=None):
    with socket.create_connection((host, port)) as sock:
        stream = sock.makefile("rw", encoding="utf-8", newline="\n")

        def send(message):
            stream.write(json.dumps(message) + "\n")
            stream.flush()

        def safe_call(function, *arguments):
            # An error in your code must not disconnect you: it is printed
            # and no order is sent for this phase.
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

        team, rules = None, None
        for line in stream:
            message = json.loads(line)
            msg_type = message["type"]
            if msg_type == "xxxxx":
                # code here ...
                pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="My AI")
    parser.add_argument("game_id", help="game ID, shown in the game window")
    parser.add_argument("--name", default="my-ai")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5555)
    parser.add_argument("--team", choices=("purple", "yellow"), default=None,
                        help="wanted team (otherwise the first free one)")
    args = parser.parse_args()
    play(args.game_id, args.name, args.host, args.port, args.team)
