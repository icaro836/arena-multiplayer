import json
import os
import random
import re
import secrets
import string
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "3000"))
ROOM_TTL = 3 * 60 * 60
ROOMS = {}
ROOMS_LOCK = threading.RLock()
ACTIONS = {"attack", "defend", "heal"}
STATIC_FILES = {"arena.html", "arena-manifest.json", "arena-sw.js"}


def public_room(room, player_id):
    return {
        "code": room["code"],
        "status": room["status"],
        "round": room["round"],
        "winnerId": room["winnerId"],
        "report": room["report"],
        "players": [
            {"id": player["id"], "name": player["name"], "life": player["life"]}
            for player in room["players"]
        ],
        "submitted": [
            player["id"] for player in room["players"] if player["action"]
        ],
        "you": player_id,
    }


def resolve_round(room):
    first, second = room["players"]
    players = (first, second)
    attacks = [
        random.randint(10, 25) if player["action"] == "attack" else 0
        for player in players
    ]
    defenses = [
        random.randint(8, 18) if player["action"] == "defend" else 0
        for player in players
    ]
    healing = [
        random.randint(15, 30) if player["action"] == "heal" else 0
        for player in players
    ]
    damage = (
        max(0, attacks[1] - defenses[0]),
        max(0, attacks[0] - defenses[1]),
    )

    for index, player in enumerate(players):
        player["life"] = max(
            0, min(100, player["life"] + healing[index]) - damage[index]
        )

    results = []
    for index, player in enumerate(players):
        details = []
        if healing[index]:
            details.append(f"curou {healing[index]}")
        if damage[index]:
            details.append(f"recebeu {damage[index]} de dano")
        if not details:
            details.append("se defendeu")
        results.append(f"{player['name']}: {', '.join(details)}")
    room["report"] = " · ".join(results)

    if first["life"] == 0 or second["life"] == 0:
        room["status"] = "finished"
        room["winnerId"] = (
            None
            if first["life"] == second["life"] == 0
            else first["id"]
            if first["life"] > 0
            else second["id"]
        )
    else:
        room["round"] += 1
        first["action"] = None
        second["action"] = None


class ArenaHandler(BaseHTTPRequestHandler):
    def send_json(self, status, value):
        content = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 10_000:
                raise ValueError("Requisição muito grande.")
            body = self.rfile.read(length)
            data = json.loads(body) if body else {}
            if not isinstance(data, dict):
                raise ValueError("Os dados precisam ser um objeto JSON.")
            return data
        except (ValueError, json.JSONDecodeError) as error:
            raise ValueError("Dados inválidos.") from error

    def expire_rooms(self):
        now = time.time()
        expired = [
            code
            for code, room in ROOMS.items()
            if now - room["updatedAt"] > ROOM_TTL
        ]
        for code in expired:
            del ROOMS[code]

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self.send_json(200, {"ok": True})
            return

        match = re.fullmatch(r"/api/rooms/([A-Z0-9]{6})", parsed.path)
        if match:
            code = match.group(1)
            player_id = parse_qs(parsed.query).get("playerId", [""])[0]
            with ROOMS_LOCK:
                self.expire_rooms()
                room = ROOMS.get(code)
                if room is None:
                    self.send_json(404, {"error": "Sala não encontrada ou expirada."})
                    return
                player = next(
                    (entry for entry in room["players"] if entry["id"] == player_id),
                    None,
                )
                if player is None:
                    self.send_json(404, {"error": "Jogador não encontrado nesta sala."})
                    return
                room["updatedAt"] = time.time()
                self.send_json(200, public_room(room, player_id))
            return

        if parsed.path.startswith("/api/"):
            self.send_json(404, {"error": "Endereço não encontrado."})
            return
        self.serve_file(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            body = self.read_json()
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
            return

        with ROOMS_LOCK:
            self.expire_rooms()
            if parsed.path == "/api/rooms":
                name = body.get("name", "")
                name = name.strip()[:18] if isinstance(name, str) else ""
                if not name:
                    self.send_json(400, {"error": "Digite um nome para criar a sala."})
                    return

                alphabet = string.ascii_uppercase + string.digits
                code = "".join(secrets.choice(alphabet) for _ in range(6))
                while code in ROOMS:
                    code = "".join(secrets.choice(alphabet) for _ in range(6))

                player = {
                    "id": str(uuid.uuid4()),
                    "name": name,
                    "life": 100,
                    "action": None,
                }
                room = {
                    "code": code,
                    "players": [player],
                    "status": "waiting",
                    "round": 1,
                    "winnerId": None,
                    "report": "",
                    "updatedAt": time.time(),
                }
                ROOMS[code] = room
                self.send_json(
                    201,
                    {
                        "session": {"code": code, "playerId": player["id"]},
                        "state": public_room(room, player["id"]),
                    },
                )
                return

            match = re.fullmatch(
                r"/api/rooms/([A-Z0-9]{6})(?:/(join|action|leave))?",
                parsed.path,
            )
            if not match:
                self.send_json(404, {"error": "Endereço não encontrado."})
                return

            code, operation = match.groups()
            room = ROOMS.get(code)
            if room is None:
                self.send_json(404, {"error": "Sala não encontrada ou expirada."})
                return
            room["updatedAt"] = time.time()

            if operation == "join":
                name = body.get("name", "")
                name = name.strip()[:18] if isinstance(name, str) else ""
                if not name:
                    self.send_json(400, {"error": "Digite seu nome para entrar na sala."})
                    return
                if room["status"] != "waiting" or len(room["players"]) != 1:
                    self.send_json(
                        409,
                        {"error": "Esta sala já está cheia ou a partida já começou."},
                    )
                    return

                player = {
                    "id": str(uuid.uuid4()),
                    "name": name,
                    "life": 100,
                    "action": None,
                }
                room["players"].append(player)
                room["status"] = "playing"
                self.send_json(
                    200,
                    {
                        "session": {"code": code, "playerId": player["id"]},
                        "state": public_room(room, player["id"]),
                    },
                )
                return

            if operation == "action":
                player = next(
                    (
                        entry
                        for entry in room["players"]
                        if entry["id"] == body.get("playerId")
                    ),
                    None,
                )
                if player is None:
                    self.send_json(404, {"error": "Jogador não encontrado nesta sala."})
                    return
                if room["status"] != "playing":
                    self.send_json(409, {"error": "A partida não está em andamento."})
                    return
                action = body.get("action")
                if action not in ACTIONS:
                    self.send_json(400, {"error": "Ação inválida."})
                    return
                if player["action"]:
                    self.send_json(
                        409, {"error": "Você já escolheu sua ação nesta rodada."}
                    )
                    return

                player["action"] = action
                if all(entry["action"] for entry in room["players"]):
                    resolve_round(room)
                self.send_json(200, public_room(room, player["id"]))
                return

            if operation == "leave":
                player_id = body.get("playerId")
                player_index = next(
                    (
                        index
                        for index, entry in enumerate(room["players"])
                        if entry["id"] == player_id
                    ),
                    None,
                )
                if player_index is None:
                    self.send_json(404, {"error": "Jogador não encontrado nesta sala."})
                    return

                room["players"].pop(player_index)
                if not room["players"]:
                    del ROOMS[code]
                elif room["status"] == "playing":
                    room["status"] = "finished"
                    room["winnerId"] = room["players"][0]["id"]
                    room["report"] = "O adversário saiu da partida."
                self.send_json(200, {"ok": True})
                return

            self.send_json(405, {"error": "Método não permitido."})

    def serve_file(self, request_path):
        relative_path = unquote(request_path.lstrip("/")) or "arena.html"
        if relative_path not in STATIC_FILES:
            self.send_error(404, "Arquivo não encontrado.")
            return
        file_path = (ROOT / relative_path).resolve()
        if ROOT not in file_path.parents:
            self.send_error(403)
            return
        if not file_path.is_file():
            self.send_error(404, "Arquivo não encontrado.")
            return

        content_type = {
            ".html": "text/html; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
        }.get(file_path.suffix, "application/octet-stream")
        content = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format_string, *args):
        print(f"{self.address_string()} - {format_string % args}")


server = ThreadingHTTPServer(("0.0.0.0", PORT), ArenaHandler)
print(f"Arena multiplayer disponível na porta {PORT}.")
server.serve_forever()
