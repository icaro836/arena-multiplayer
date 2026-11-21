import json
import math
import os
import random
import re
import secrets
import string
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


WIDTH = 900
HEIGHT = 540
PORT = int(os.environ.get("PORT", "3000"))
ROOM_TTL = 3 * 60 * 60
MAX_ROOMS = 200
ROOMS = {}
ROOMS_LOCK = threading.RLock()
PLAYER_RADIUS = 19
BULLET_RADIUS = 5
PLAYER_SPEED = 230
BULLET_SPEED = 520
FIRE_COOLDOWN = 0.32
WIN_SCORE = 5


PAGE = r"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
  <meta name="theme-color" content="#0b1020">
  <title>GG - Tiro Online</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, Arial, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #0b1020; color: #eef2ff; }
    main { width: min(96vw, 900px); padding: 18px; }
    h1 { margin: 0 0 12px; text-align: center; font-size: clamp(26px, 6vw, 40px); letter-spacing: 2px; }
    .panel { padding: 18px; border: 1px solid #29334e; border-radius: 16px; background: #141c30; box-shadow: 0 20px 55px #0006; }
    .lobby { width: min(100%, 430px); margin: 0 auto; text-align: center; }
    p { line-height: 1.45; color: #cbd5e1; }
    input { width: 100%; min-height: 50px; margin: 8px 0; padding: 12px; border: 1px solid #46536e; border-radius: 10px; background: #0b1020; color: white; font-size: 17px; text-align: center; }
    button { min-height: 48px; padding: 10px 16px; border: 0; border-radius: 10px; background: #2563eb; color: white; font-size: 16px; font-weight: 750; cursor: pointer; touch-action: none; user-select: none; }
    button:active { transform: scale(.97); }
    button.secondary { background: #39445d; }
    .lobby button { width: 100%; margin: 5px 0; }
    .error { min-height: 22px; color: #fca5a5; }
    .room { margin: 8px auto 12px; text-align: center; color: #bfdbfe; font-weight: 800; letter-spacing: 4px; font-size: 20px; }
    .status { min-height: 24px; text-align: center; color: #cbd5e1; }
    .scoreboard { display: flex; justify-content: center; gap: 12px; flex-wrap: wrap; margin: 10px 0; }
    .score { min-width: 130px; padding: 8px 12px; border-radius: 9px; background: #263149; text-align: center; }
    .score strong { color: #fff; }
    .game-wrap { overflow: hidden; border: 1px solid #46536e; border-radius: 12px; background: #080d18; }
    canvas { display: block; width: 100%; height: auto; aspect-ratio: 5 / 3; touch-action: none; }
    .help { margin: 10px 0 4px; text-align: center; font-size: 13px; }
    .controls { display: none; align-items: center; justify-content: space-between; gap: 12px; padding-top: 12px; }
    .dpad { display: grid; grid-template-columns: repeat(3, 48px); grid-template-rows: repeat(2, 46px); gap: 4px; }
    .dpad button { min-height: 44px; padding: 4px; background: #33415f; font-size: 19px; }
    .dpad [data-key="w"] { grid-column: 2; grid-row: 1; }
    .dpad [data-key="a"] { grid-column: 1; grid-row: 2; }
    .dpad [data-key="s"] { grid-column: 2; grid-row: 2; }
    .dpad [data-key="d"] { grid-column: 3; grid-row: 2; }
    #fire { width: 112px; height: 74px; border-radius: 18px; background: #dc2626; font-size: 18px; }
    .exit { display: block; width: 100%; margin-top: 12px; }
    @media (pointer: coarse), (max-width: 700px) {
      main { width: 100%; padding: 8px; }
      .panel { padding: 12px; }
      .controls { display: flex; }
      .help { font-size: 12px; }
    }
  </style>
</head>
<body>
  <main>
    <h1>GG · TIRO ONLINE</h1>
    <section class="panel"><div id="screen"></div></section>
  </main>
  <script>
    const W = 900;
    const H = 540;
    const screen = document.getElementById("screen");
    let canvas = null;
    let ctx = null;
    const keys = new Set();
    let session = null;
    let state = null;
    let syncTimer = null;
    let busy = false;
    let firing = false;
    let aim = { x: W / 2, y: H / 2 };
    let notice = "";

    try {
      session = JSON.parse(localStorage.getItem("ggSession") || "null");
    } catch {
      localStorage.removeItem("ggSession");
    }
    if (!session || !/^[A-Z0-9]{6}$/.test(session.code || "") || typeof session.playerId !== "string") {
      session = null;
    }

    function escapeHTML(value) {
      return String(value).replace(/[&<>"']/g, (char) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"
      })[char]);
    }

    async function api(url, options = {}) {
      const response = await fetch(url, {
        ...options,
        headers: { "Content-Type": "application/json", ...options.headers }
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Não foi possível completar a operação.");
      return result;
    }

    function lobby(error = "") {
      clearInterval(syncTimer);
      session = null;
      state = null;
      screen.innerHTML = `
        <div class="lobby">
          <p>Crie uma sala e mande o código para outra pessoa. Cada jogador controla um piloto e tenta fazer 5 pontos.</p>
          <input id="name" maxlength="16" placeholder="Seu nome" autocomplete="nickname">
          <button id="create">Criar sala</button>
          <p>ou entre em uma sala:</p>
          <input id="code" maxlength="6" placeholder="Código da sala" autocomplete="off">
          <button id="join" class="secondary">Entrar na sala</button>
          <p class="error">${escapeHTML(error || notice)}</p>
        </div>`;
      document.getElementById("create").addEventListener("click", createRoom);
      document.getElementById("join").addEventListener("click", joinRoom);
    }

    function playerName() {
      const name = document.getElementById("name").value.trim();
      if (!name) throw new Error("Digite seu nome primeiro.");
      return name;
    }

    async function createRoom() {
      try {
        const result = await api("/api/rooms", {
          method: "POST",
          body: JSON.stringify({ name: playerName() })
        });
        enterRoom(result);
      } catch (error) {
        lobby(error.message);
      }
    }

    async function joinRoom() {
      try {
        const name = playerName();
        const code = document.getElementById("code").value.trim().toUpperCase();
        const result = await api(`/api/rooms/${encodeURIComponent(code)}/join`, {
          method: "POST",
          body: JSON.stringify({ name })
        });
        enterRoom(result);
      } catch (error) {
        lobby(error.message);
      }
    }

    function enterRoom(result) {
      session = result.session;
      state = result.state;
      localStorage.setItem("ggSession", JSON.stringify(session));
      notice = "";
      renderGame();
      syncTimer = setInterval(sync, 90);
    }

    function installControls() {
      const gameCanvas = document.getElementById("field");
      gameCanvas.addEventListener("pointermove", updateAim);
      gameCanvas.addEventListener("pointerdown", (event) => {
        updateAim(event);
        if (event.pointerType === "mouse" && event.button === 0) firing = true;
      });
      window.addEventListener("pointerup", () => { firing = false; });
      document.querySelectorAll("[data-key]").forEach((button) => {
        const key = button.dataset.key;
        button.addEventListener("pointerdown", (event) => {
          event.preventDefault();
          keys.add(key);
        });
        for (const type of ["pointerup", "pointercancel", "pointerleave"]) {
          button.addEventListener(type, () => keys.delete(key));
        }
      });
      const fireButton = document.getElementById("fire");
      fireButton.addEventListener("pointerdown", (event) => {
        event.preventDefault();
        firing = true;
      });
      for (const type of ["pointerup", "pointercancel", "pointerleave"]) {
        fireButton.addEventListener(type, () => { firing = false; });
      }
      window.addEventListener("keydown", (event) => {
        const key = event.key.toLowerCase();
        if (["w", "a", "s", "d", "arrowup", "arrowleft", "arrowdown", "arrowright", " "].includes(key)) {
          event.preventDefault();
          keys.add(key);
          if (key === " ") firing = true;
        }
      });
      window.addEventListener("keyup", (event) => {
        const key = event.key.toLowerCase();
        keys.delete(key);
        if (key === " ") firing = false;
      });
      document.getElementById("exit").addEventListener("click", leaveRoom);
    }

    function updateAim(event) {
      const rect = canvas.getBoundingClientRect();
      aim = {
        x: Math.max(0, Math.min(W, (event.clientX - rect.left) * W / rect.width)),
        y: Math.max(0, Math.min(H, (event.clientY - rect.top) * H / rect.height))
      };
    }

    function currentMove() {
      let x = 0;
      let y = 0;
      if (keys.has("a") || keys.has("arrowleft")) x -= 1;
      if (keys.has("d") || keys.has("arrowright")) x += 1;
      if (keys.has("w") || keys.has("arrowup")) y -= 1;
      if (keys.has("s") || keys.has("arrowdown")) y += 1;
      const length = Math.hypot(x, y) || 1;
      return { x: x / length, y: y / length };
    }

    async function sync() {
      if (!session || busy) return;
      busy = true;
      try {
        state = await api(`/api/rooms/${encodeURIComponent(session.code)}/input`, {
          method: "POST",
          body: JSON.stringify({
            playerId: session.playerId,
            move: currentMove(),
            aim,
            fire: firing
          })
        });
        notice = "";
        renderGame();
      } catch (error) {
        notice = error.message;
        if (/sala|jogador/i.test(error.message)) {
          lobby(error.message);
        } else {
          const message = document.getElementById("status");
          if (message) message.textContent = `Conexão instável: ${notice}`;
        }
      } finally {
        busy = false;
      }
    }

    function renderGame() {
      if (!session || !state) return lobby(notice);
      const mine = state.players.find((player) => player.id === session.playerId);
      const enemy = state.players.find((player) => player.id !== session.playerId);
      if (!mine) return lobby("Sua sessão não está mais nesta sala.");

      if (state.status === "finished") {
        clearInterval(syncTimer);
        const outcome = state.winnerId === session.playerId ? "Você venceu!" :
          state.winnerId ? "Você perdeu!" : "Empate!";
        screen.innerHTML = `
          <div class="lobby">
            <div class="room">SALA ${escapeHTML(state.code)}</div>
            <h2>${outcome}</h2>
            <p>${escapeHTML(state.report || "A partida terminou.")}</p>
            <p>${state.players.map((player) =>
              `${escapeHTML(player.name)}: ${player.score} ponto(s)`
            ).join("<br>")}</p>
            <button id="back">Voltar ao início</button>
          </div>`;
        document.getElementById("back").addEventListener("click", leaveRoom);
        return;
      }

      if (document.getElementById("field")) {
        updateGameView(mine, enemy);
        return;
      }

      screen.innerHTML = `
        <div class="room">SALA ${escapeHTML(state.code)}</div>
        <div class="scoreboard">
          <div class="score" id="mine"></div>
          <div class="score" id="enemy"></div>
        </div>
        <p class="status" id="status"></p>
        <div class="game-wrap"><canvas id="field" width="${W}" height="${H}"></canvas></div>
        <p class="help">Computador: WASD ou setas para mover, mouse para mirar, clique ou Espaço para atirar.<br>Celular: use os controles e toque na arena para mirar.</p>
        <div class="controls">
          <div class="dpad">
            <button data-key="w" aria-label="Mover para cima">▲</button>
            <button data-key="a" aria-label="Mover para esquerda">◀</button>
            <button data-key="s" aria-label="Mover para baixo">▼</button>
            <button data-key="d" aria-label="Mover para direita">▶</button>
          </div>
          <button id="fire">ATIRAR</button>
        </div>
        <button id="exit" class="secondary exit">Sair da partida</button>`;
      canvas = document.getElementById("field");
      ctx = canvas.getContext("2d");
      installControls();
      updateGameView(mine, enemy);
    }

    function updateGameView(mine, enemy) {
      const mineCard = document.getElementById("mine");
      const enemyCard = document.getElementById("enemy");
      const status = document.getElementById("status");
      if (!mineCard || !enemyCard || !status) return;
      mineCard.innerHTML = `🔵 ${escapeHTML(mine.name)} — ${mine.health} vida · ${mine.score}/${state.target}`;
      enemyCard.innerHTML = enemy
        ? `🔴 ${escapeHTML(enemy.name)} — ${enemy.health} vida · ${enemy.score}/${state.target}`
        : "Aguardando adversário...";
      status.textContent = state.status === "waiting"
        ? "Envie o código da sala para seu adversário."
        : notice || state.report || "Dispute os pontos! Primeiro a fazer 5 vence.";
      draw(mine);
    }

    function draw(mine) {
      ctx.fillStyle = "#0b1325";
      ctx.fillRect(0, 0, W, H);
      ctx.strokeStyle = "#1c2943";
      ctx.lineWidth = 1;
      for (let x = 0; x <= W; x += 45) {
        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
      }
      for (let y = 0; y <= H; y += 45) {
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
      }
      ctx.strokeStyle = "#586987";
      ctx.lineWidth = 5;
      ctx.strokeRect(3, 3, W - 6, H - 6);

      for (const bullet of state.bullets) {
        ctx.beginPath();
        ctx.fillStyle = bullet.ownerId === session.playerId ? "#67e8f9" : "#fbbf24";
        ctx.shadowColor = ctx.fillStyle;
        ctx.shadowBlur = 12;
        ctx.arc(bullet.x, bullet.y, 6, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.shadowBlur = 0;

      for (const player of state.players) {
        ctx.beginPath();
        ctx.fillStyle = player.id === session.playerId ? "#3b82f6" : "#ef4444";
        ctx.arc(player.x, player.y, 19, 0, Math.PI * 2);
        ctx.fill();
        ctx.lineWidth = 3;
        ctx.strokeStyle = player.id === session.playerId ? "#bfdbfe" : "#fecaca";
        ctx.stroke();
        if (player.invulnerable) {
          ctx.beginPath();
          ctx.setLineDash([5, 5]);
          ctx.strokeStyle = "#fef08a";
          ctx.arc(player.x, player.y, 26, 0, Math.PI * 2);
          ctx.stroke();
          ctx.setLineDash([]);
        }
        ctx.fillStyle = "#fff";
        ctx.font = "bold 14px system-ui";
        ctx.textAlign = "center";
        ctx.fillText(player.name, player.x, player.y - 28);
      }
      ctx.beginPath();
      ctx.strokeStyle = "#a7f3d0";
      ctx.lineWidth = 2;
      ctx.arc(aim.x, aim.y, 9, 0, Math.PI * 2);
      ctx.moveTo(aim.x - 14, aim.y); ctx.lineTo(aim.x - 5, aim.y);
      ctx.moveTo(aim.x + 5, aim.y); ctx.lineTo(aim.x + 14, aim.y);
      ctx.moveTo(aim.x, aim.y - 14); ctx.lineTo(aim.x, aim.y - 5);
      ctx.moveTo(aim.x, aim.y + 5); ctx.lineTo(aim.x, aim.y + 14);
      ctx.stroke();
    }

    async function leaveRoom() {
      if (session) {
        try {
          await api(`/api/rooms/${encodeURIComponent(session.code)}/leave`, {
            method: "POST",
            body: JSON.stringify({ playerId: session.playerId })
          });
        } catch {
          // A sala pode ter expirado; a sessão local ainda pode ser encerrada.
        }
      }
      clearInterval(syncTimer);
      localStorage.removeItem("ggSession");
      keys.clear();
      session = null;
      state = null;
      lobby();
    }

    async function resume() {
      try {
        state = await api(`/api/rooms/${encodeURIComponent(session.code)}/input`, {
          method: "POST",
          body: JSON.stringify({
            playerId: session.playerId,
            move: { x: 0, y: 0 },
            aim,
            fire: false
          })
        });
        renderGame();
        syncTimer = setInterval(sync, 90);
      } catch (error) {
        localStorage.removeItem("ggSession");
        session = null;
        lobby(error.message);
      }
    }

    if (session) resume();
    else lobby();
  </script>
</body>
</html>"""


def new_player(name, slot):
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "x": 85 if slot == 0 else WIDTH - 85,
        "y": HEIGHT / 2,
        "health": 3,
        "score": 0,
        "move": {"x": 0.0, "y": 0.0},
        "aim": {"x": WIDTH / 2, "y": HEIGHT / 2},
        "fire": False,
        "lastShot": 0.0,
        "invulnerableUntil": 0.0,
    }


def public_room(room, player_id):
    now = time.monotonic()
    return {
        "code": room["code"],
        "status": room["status"],
        "round": room["round"],
        "target": WIN_SCORE,
        "winnerId": room["winnerId"],
        "report": room["report"],
        "players": [
            {
                "id": player["id"],
                "name": player["name"],
                "x": round(player["x"], 1),
                "y": round(player["y"], 1),
                "health": player["health"],
                "score": player["score"],
                "invulnerable": player["invulnerableUntil"] > now,
            }
            for player in room["players"]
        ],
        "bullets": [
            {"x": round(bullet["x"], 1), "y": round(bullet["y"], 1), "ownerId": bullet["ownerId"]}
            for bullet in room["bullets"]
        ],
        "you": player_id,
    }


def advance_room(room, now):
    if room["status"] != "playing":
        room["lastTick"] = now
        return
    dt = min(max(now - room["lastTick"], 0.0), 0.1)
    room["lastTick"] = now
    if dt == 0:
        return

    for player in room["players"]:
        move = player["move"]
        player["x"] = min(WIDTH - PLAYER_RADIUS, max(PLAYER_RADIUS, player["x"] + move["x"] * PLAYER_SPEED * dt))
        player["y"] = min(HEIGHT - PLAYER_RADIUS, max(PLAYER_RADIUS, player["y"] + move["y"] * PLAYER_SPEED * dt))
        if player["fire"] and now - player["lastShot"] >= FIRE_COOLDOWN and len(room["bullets"]) < 100:
            dx = player["aim"]["x"] - player["x"]
            dy = player["aim"]["y"] - player["y"]
            length = math.hypot(dx, dy)
            if length > 0:
                room["bullets"].append({
                    "x": player["x"] + dx / length * (PLAYER_RADIUS + BULLET_RADIUS),
                    "y": player["y"] + dy / length * (PLAYER_RADIUS + BULLET_RADIUS),
                    "vx": dx / length * BULLET_SPEED,
                    "vy": dy / length * BULLET_SPEED,
                    "ownerId": player["id"],
                    "created": now,
                })
                player["lastShot"] = now

    remaining = []
    for bullet in room["bullets"]:
        bullet["x"] += bullet["vx"] * dt
        bullet["y"] += bullet["vy"] * dt
        if (
            now - bullet["created"] > 2.2
            or bullet["x"] < 0
            or bullet["x"] > WIDTH
            or bullet["y"] < 0
            or bullet["y"] > HEIGHT
        ):
            continue

        hit = None
        for player in room["players"]:
            if player["id"] == bullet["ownerId"] or player["invulnerableUntil"] > now:
                continue
            if math.hypot(player["x"] - bullet["x"], player["y"] - bullet["y"]) <= PLAYER_RADIUS + BULLET_RADIUS:
                hit = player
                break

        if hit is None:
            remaining.append(bullet)
            continue

        shooter = next((player for player in room["players"] if player["id"] == bullet["ownerId"]), None)
        if shooter is None:
            continue
        hit["health"] -= 1
        if hit["health"] <= 0:
            shooter["score"] += 1
            room["report"] = f"{shooter['name']} marcou um ponto!"
            if shooter["score"] >= WIN_SCORE:
                room["status"] = "finished"
                room["winnerId"] = shooter["id"]
                room["bullets"] = []
                return
            hit["health"] = 3
            hit["x"] = 85 if hit is room["players"][0] else WIDTH - 85
            hit["y"] = HEIGHT / 2
            hit["invulnerableUntil"] = now + 1.2
        else:
            room["report"] = f"{hit['name']} levou um tiro!"
    room["bullets"] = remaining
    room["round"] += 1


class GameHandler(BaseHTTPRequestHandler):
    def send_json(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 10_000:
                raise ValueError
            value = json.loads(self.rfile.read(length)) if length else {}
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, json.JSONDecodeError) as error:
            raise ValueError("Envie um objeto JSON válido.") from error

    def clean_rooms(self):
        now = time.monotonic()
        for code in [
            code for code, room in ROOMS.items()
            if now - room["updatedAt"] > ROOM_TTL
        ]:
            del ROOMS[code]

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            self.send_json(200, {"ok": True})
        elif path == "/":
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404, "Não encontrado.")

    def do_POST(self):
        try:
            body = self.read_json()
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
            return

        path = urlparse(self.path).path
        with ROOMS_LOCK:
            self.clean_rooms()
            if path == "/api/rooms":
                name = body.get("name", "")
                name = name.strip()[:16] if isinstance(name, str) else ""
                if not name:
                    self.send_json(400, {"error": "Digite seu nome para criar a sala."})
                    return
                if len(ROOMS) >= MAX_ROOMS:
                    self.send_json(503, {"error": "Muitas salas abertas. Tente novamente mais tarde."})
                    return
                alphabet = string.ascii_uppercase + string.digits
                code = "".join(secrets.choice(alphabet) for _ in range(6))
                while code in ROOMS:
                    code = "".join(secrets.choice(alphabet) for _ in range(6))
                player = new_player(name, 0)
                room = {
                    "code": code,
                    "players": [player],
                    "bullets": [],
                    "status": "waiting",
                    "round": 0,
                    "winnerId": None,
                    "report": "",
                    "lastTick": time.monotonic(),
                    "updatedAt": time.monotonic(),
                }
                ROOMS[code] = room
                self.send_json(201, {
                    "session": {"code": code, "playerId": player["id"]},
                    "state": public_room(room, player["id"]),
                })
                return

            match = re.fullmatch(
                r"/api/rooms/([A-Z0-9]{6})(?:/(join|input|leave))?",
                path,
            )
            if not match:
                self.send_json(404, {"error": "Endereço não encontrado."})
                return
            code, operation = match.groups()
            room = ROOMS.get(code)
            if room is None:
                self.send_json(404, {"error": "Sala não encontrada ou expirada."})
                return
            now = time.monotonic()
            room["updatedAt"] = now

            if operation == "join":
                name = body.get("name", "")
                name = name.strip()[:16] if isinstance(name, str) else ""
                if not name:
                    self.send_json(400, {"error": "Digite seu nome para entrar na sala."})
                    return
                if room["status"] != "waiting" or len(room["players"]) != 1:
                    self.send_json(409, {"error": "Essa sala já está cheia ou a partida começou."})
                    return
                player = new_player(name, 1)
                room["players"].append(player)
                room["status"] = "playing"
                room["lastTick"] = now
                self.send_json(200, {
                    "session": {"code": code, "playerId": player["id"]},
                    "state": public_room(room, player["id"]),
                })
                return

            player = next(
                (entry for entry in room["players"] if entry["id"] == body.get("playerId")),
                None,
            )
            if player is None:
                self.send_json(404, {"error": "Jogador não encontrado nesta sala."})
                return

            if operation == "input":
                if room["status"] == "playing":
                    move = body.get("move", {})
                    aim_value = body.get("aim", {})
                    if not isinstance(move, dict) or not isinstance(aim_value, dict):
                        self.send_json(400, {"error": "Controles inválidos."})
                        return
                    for axis in ("x", "y"):
                        value = move.get(axis, 0)
                        if not isinstance(value, (int, float)) or not math.isfinite(value):
                            self.send_json(400, {"error": "Movimento inválido."})
                            return
                        player["move"][axis] = max(-1.0, min(1.0, float(value)))
                        value = aim_value.get(axis, WIDTH / 2 if axis == "x" else HEIGHT / 2)
                        if not isinstance(value, (int, float)) or not math.isfinite(value):
                            self.send_json(400, {"error": "Mira inválida."})
                            return
                        player["aim"][axis] = max(
                            0.0,
                            min(float(WIDTH if axis == "x" else HEIGHT), float(value)),
                        )
                    player["fire"] = body.get("fire") is True
                    advance_room(room, now)
                self.send_json(200, public_room(room, player["id"]))
                return

            if operation == "leave":
                room["players"].remove(player)
                if not room["players"]:
                    del ROOMS[code]
                elif room["status"] == "playing":
                    room["status"] = "finished"
                    room["winnerId"] = room["players"][0]["id"]
                    room["report"] = "O adversário saiu da partida."
                self.send_json(200, {"ok": True})
                return

            self.send_json(405, {"error": "Método não permitido."})

    def log_message(self, format_string, *args):
        print(f"{self.address_string()} - {format_string % args}")


server = ThreadingHTTPServer(("0.0.0.0", PORT), GameHandler)
print(f"GG Tiro Online disponível na porta {PORT}.")
server.serve_forever()