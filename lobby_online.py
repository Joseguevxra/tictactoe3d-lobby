"""Lobby y relay HTTP para Tic Tac Toe 3D.

Corre en Render con:
    python lobby_online.py
"""

import http.server
import json
import os
import threading
import time
import urllib.parse
import uuid

TAMANO = 4
RANGO = range(TAMANO)
MAXIMO = TAMANO - 1
JUGADOR_X = "X"
JUGADOR_O = "O"
VACIO = None

MOVE = "move"
STATE = "state"
INVALID_MOVE = "invalid_move"
WIN = "win"
DRAW = "draw"
RESTART_REQUEST = "restart_request"
RESTART_RESPONSE = "restart_response"
ERROR = "error"
CHAT = "chat"
PING = "ping"
PONG = "pong"

PARTIDAS = {}
CANDADO = threading.Lock()
LIMITE_INACTIVIDAD_S = 60
LIMITE_PRESENCIA_S = 20
INTERVALO_SONDEO_S = 0.4


def generar_lineas_ganadoras():
    lineas = []
    lineas += [tuple((x, y, z) for x in RANGO) for y in RANGO for z in RANGO]
    lineas += [tuple((x, y, z) for y in RANGO) for x in RANGO for z in RANGO]
    lineas += [tuple((x, y, z) for z in RANGO) for x in RANGO for y in RANGO]
    lineas += [tuple((i, i, z) for i in RANGO) for z in RANGO]
    lineas += [tuple((i, MAXIMO - i, z) for i in RANGO) for z in RANGO]
    lineas += [tuple((x, i, i) for i in RANGO) for x in RANGO]
    lineas += [tuple((x, i, MAXIMO - i) for i in RANGO) for x in RANGO]
    lineas += [tuple((i, y, i) for i in RANGO) for y in RANGO]
    lineas += [tuple((i, y, MAXIMO - i) for i in RANGO) for y in RANGO]
    lineas += [
        tuple((i, i, i) for i in RANGO),
        tuple((i, i, MAXIMO - i) for i in RANGO),
        tuple((i, MAXIMO - i, i) for i in RANGO),
        tuple((MAXIMO - i, i, i) for i in RANGO),
    ]
    return lineas


LINEAS_GANADORAS = generar_lineas_ganadoras()


def mapa_lineas():
    mapa = {(x, y, z): [] for x in RANGO for y in RANGO for z in RANGO}
    for linea in LINEAS_GANADORAS:
        for casilla in linea:
            mapa[casilla].append(linea)
    return mapa


MAPA_CASILLA_LINEAS = mapa_lineas()


class TicTacToe3D:
    def __init__(self):
        self.reiniciar(JUGADOR_X)

    def reiniciar(self, jugador_inicial=JUGADOR_X):
        self.tablero = {(x, y, z): VACIO for x in RANGO for y in RANGO
                        for z in RANGO}
        self.turno_actual = jugador_inicial
        self.terminado = False
        self.ganador = None
        self.linea_ganadora = None
        self.empate = False
        self.jugadas_realizadas = 0

    def estado_del_juego(self):
        return {
            "tablero": {f"{x},{y},{z}": simbolo
                        for (x, y, z), simbolo in self.tablero.items()},
            "turno_actual": self.turno_actual,
            "terminado": self.terminado,
            "ganador": self.ganador,
            "linea_ganadora": self.linea_ganadora,
            "empate": self.empate,
            "jugadas_realizadas": self.jugadas_realizadas,
        }

    def realizar_jugada(self, x, y, z):
        coord = (x, y, z)
        if self.terminado:
            return self._invalida("La partida ya termino")
        if not all(0 <= v < TAMANO for v in coord):
            return self._invalida("Coordenada fuera de rango")
        if self.tablero[coord] is not VACIO:
            return self._invalida("Casilla ocupada")
        simbolo = self.turno_actual
        self.tablero[coord] = simbolo
        self.jugadas_realizadas += 1
        linea = self._buscar_linea(coord, simbolo)
        if linea is not None:
            self.terminado = True
            self.ganador = simbolo
            self.linea_ganadora = linea
        elif self.jugadas_realizadas == TAMANO ** 3:
            self.terminado = True
            self.empate = True
        else:
            self.turno_actual = JUGADOR_O if simbolo == JUGADOR_X else JUGADOR_X
        return {"valida": True, "mensaje": "Jugada realizada",
                "ganador": self.ganador, "linea_ganadora": self.linea_ganadora,
                "empate": self.empate}

    def _invalida(self, mensaje):
        return {"valida": False, "mensaje": mensaje, "ganador": self.ganador,
                "linea_ganadora": self.linea_ganadora, "empate": self.empate}

    def _buscar_linea(self, coord, simbolo):
        for linea in MAPA_CASILLA_LINEAS[coord]:
            if all(self.tablero[c] == simbolo for c in linea):
                return linea
        return None


def nuevo_token():
    return uuid.uuid4().hex


def nuevo_game_id():
    return uuid.uuid4().hex[:8]


def encolar(partida, simbolo, mensaje):
    partida["colas"][simbolo].append(mensaje)


def difundir(partida, mensaje):
    encolar(partida, JUGADOR_X, mensaje)
    encolar(partida, JUGADOR_O, mensaje)


def mensaje_estado(partida, ultima_jugada=None, reinicio=False,
                   rival_salio=False):
    return {"tipo": STATE, "estado": partida["juego"].estado_del_juego(),
            "ultima_jugada": ultima_jugada, "reinicio": reinicio,
            "rival_salio": rival_salio,
            "jugadores": partida.get("jugadores", {})}


def simbolo_por_token(partida, token):
    for simbolo, valor in partida["tokens"].items():
        if valor == token:
            return simbolo
    return None


def cantidad_jugadores(partida):
    return sum(1 for token in partida["tokens"].values() if token)


def liberar_jugador(partida, simbolo, ahora=None):
    """Libera un puesto y deja la lobby disponible para otro jugador."""
    if not partida["tokens"].get(simbolo):
        return
    ahora = ahora or time.time()
    partida["tokens"][simbolo] = None
    partida.setdefault("jugadores", {})[simbolo] = None
    partida.setdefault("ultima_actividad", {})[simbolo] = None
    partida["colas"][simbolo].clear()
    partida["iniciada"] = False
    partida["ultimo_cambio"] = ahora
    partida["juego"].reiniciar(JUGADOR_X)
    for otro in (JUGADOR_X, JUGADOR_O):
        if partida["tokens"].get(otro):
            encolar(partida, otro, mensaje_estado(
                partida, reinicio=True, rival_salio=True))


def limpiar_partidas():
    ahora = time.time()
    caducadas = []
    for game_id, partida in list(PARTIDAS.items()):
        actividad = partida.setdefault("ultima_actividad", {})
        for simbolo in (JUGADOR_X, JUGADOR_O):
            ultima = actividad.get(simbolo)
            if (partida["tokens"].get(simbolo) and ultima is not None
                    and ahora - ultima > LIMITE_PRESENCIA_S):
                liberar_jugador(partida, simbolo, ahora)
        if (cantidad_jugadores(partida) == 0
                and ahora - partida.get("ultimo_cambio", partida["creada"])
                > LIMITE_INACTIVIDAD_S):
            caducadas.append(game_id)
    for game_id in caducadas:
        PARTIDAS.pop(game_id, None)


def procesar_jugada(partida, simbolo, mensaje):
    try:
        x, y, z = int(mensaje["x"]), int(mensaje["y"]), int(mensaje["z"])
    except (KeyError, TypeError, ValueError):
        encolar(partida, simbolo, {"tipo": INVALID_MOVE,
                                   "mensaje": "Mensaje de jugada incompleto"})
        return
    juego = partida["juego"]
    if not partida["iniciada"]:
        rechazo = "Espera a que se conecte el segundo jugador"
    elif juego.terminado:
        rechazo = "La partida ya termino"
    elif simbolo != juego.turno_actual:
        rechazo = "No es su turno"
    else:
        resultado = juego.realizar_jugada(x, y, z)
        rechazo = None if resultado["valida"] else resultado["mensaje"]
    if rechazo is not None:
        encolar(partida, simbolo, {"tipo": INVALID_MOVE, "mensaje": rechazo})
        return
    difundir(partida, mensaje_estado(
        partida, {"x": x, "y": y, "z": z, "simbolo": simbolo}))
    if resultado["ganador"] is not None:
        difundir(partida, {"tipo": WIN, "simbolo": resultado["ganador"],
                           "linea": [list(c) for c in
                                     resultado["linea_ganadora"]]})
    elif resultado["empate"]:
        difundir(partida, {"tipo": DRAW})


def procesar_mensaje(partida, simbolo, mensaje):
    tipo = mensaje.get("tipo")
    if tipo == MOVE:
        procesar_jugada(partida, simbolo, mensaje)
    elif tipo == RESTART_REQUEST:
        rival = JUGADOR_O if simbolo == JUGADOR_X else JUGADOR_X
        encolar(partida, rival, {"tipo": RESTART_REQUEST, "de": simbolo})
    elif tipo == RESTART_RESPONSE:
        acepta = bool(mensaje.get("acepta"))
        if acepta:
            partida["juego"].reiniciar(JUGADOR_X)
            difundir(partida, {"tipo": RESTART_RESPONSE, "acepta": True})
            difundir(partida, mensaje_estado(partida, reinicio=True))
        else:
            rival = JUGADOR_O if simbolo == JUGADOR_X else JUGADOR_X
            encolar(partida, rival, {"tipo": RESTART_RESPONSE,
                                     "acepta": False})
    elif tipo == CHAT:
        texto = str(mensaje.get("texto", ""))[:200]
        if texto.strip():
            difundir(partida, {"tipo": CHAT, "de": simbolo, "texto": texto})
    elif tipo == PING:
        encolar(partida, simbolo, {"tipo": PONG, "t": mensaje.get("t")})
    else:
        encolar(partida, simbolo, {"tipo": ERROR,
                                   "mensaje": f"Tipo desconocido: {tipo}"})


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "TicTacToe3DLobby/1.0"

    def log_message(self, formato, *args):
        return

    def leer_json(self):
        largo = int(self.headers.get("Content-Length", "0") or "0")
        if largo <= 0:
            return {}
        return json.loads(self.rfile.read(largo).decode("utf-8") or "{}")

    def responder(self, datos, codigo=200):
        cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def error_json(self, mensaje, codigo=200):
        self.responder({"error": mensaje}, codigo)

    def do_POST(self):
        try:
            datos = self.leer_json()
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            self.error_json("JSON invalido")
            return
        if self.path == "/crear":
            self.crear(datos)
        elif self.path == "/unir":
            self.unir(datos)
        elif self.path == "/salir":
            self.salir(datos)
        elif self.path == "/enviar":
            self.enviar(datos)
        else:
            self.error_json("Endpoint no encontrado", 404)

    def do_GET(self):
        ruta = urllib.parse.urlparse(self.path)
        if ruta.path == "/lista":
            self.lista()
        elif ruta.path == "/recibir":
            self.recibir(ruta)
        else:
            self.error_json("Endpoint no encontrado", 404)

    def crear(self, datos):
        nombre_lobby = (str(datos.get("lobby") or datos.get("nombre")
                            or "Partida").strip()[:60] or "Partida")
        nombre_jugador = (str(datos.get("jugador")
                              or datos.get("nombre_jugador")
                              or "Jugador").strip()[:40] or "Jugador")
        with CANDADO:
            limpiar_partidas()
            nombre_normalizado = nombre_lobby.casefold()
            if any(p["nombre"].strip().casefold() == nombre_normalizado
                   for p in PARTIDAS.values()):
                self.error_json(
                    "Ya existe una partida con ese nombre. Elige otro nombre.")
                return
            game_id = nuevo_game_id()
            while game_id in PARTIDAS:
                game_id = nuevo_game_id()
            token = nuevo_token()
            ahora = time.time()
            PARTIDAS[game_id] = {
                "nombre": nombre_lobby,
                "juego": TicTacToe3D(),
                "tokens": {JUGADOR_X: token, JUGADOR_O: None},
                "jugadores": {JUGADOR_X: nombre_jugador, JUGADOR_O: None},
                "colas": {JUGADOR_X: [], JUGADOR_O: []},
                "ultima_actividad": {JUGADOR_X: ahora, JUGADOR_O: None},
                "iniciada": False,
                "creada": ahora,
                "ultimo_cambio": ahora,
            }
            estado = mensaje_estado(PARTIDAS[game_id])
        self.responder({"game_id": game_id, "simbolo": JUGADOR_X,
                        "token": token, "estado": estado})

    def lista(self):
        with CANDADO:
            limpiar_partidas()
            partidas = [
                {"game_id": gid, "nombre": p["nombre"],
                 "jugadores": cantidad_jugadores(p), "capacidad": 2}
                for gid, p in PARTIDAS.items()]
        self.responder({"partidas": partidas})

    def unir(self, datos):
        game_id = str(datos.get("game_id") or "")
        nombre_jugador = (str(datos.get("jugador") or datos.get("nombre")
                              or "Jugador").strip()[:40] or "Jugador")
        with CANDADO:
            limpiar_partidas()
            partida = PARTIDAS.get(game_id)
            if partida is None:
                self.error_json("La partida no existe")
                return
            libres = [simbolo for simbolo in (JUGADOR_X, JUGADOR_O)
                      if not partida["tokens"].get(simbolo)]
            if not libres:
                self.error_json("La partida ya esta llena")
                return
            simbolo = libres[0]
            token = nuevo_token()
            ahora = time.time()
            partida["tokens"][simbolo] = token
            partida.setdefault("jugadores", {})[simbolo] = nombre_jugador
            partida.setdefault("ultima_actividad", {})[simbolo] = ahora
            partida["iniciada"] = cantidad_jugadores(partida) == 2
            partida["ultimo_cambio"] = ahora
            estado = mensaje_estado(partida)
            difundir(partida, mensaje_estado(partida))
        self.responder({"game_id": game_id, "simbolo": simbolo,
                        "token": token, "estado": estado})

    def salir(self, datos):
        game_id = str(datos.get("game_id") or "")
        token = str(datos.get("token") or "")
        with CANDADO:
            partida = PARTIDAS.get(game_id)
            if partida is None:
                self.responder({"ok": True})
                return
            simbolo = simbolo_por_token(partida, token)
            if simbolo is not None:
                liberar_jugador(partida, simbolo)
        self.responder({"ok": True})

    def enviar(self, datos):
        game_id = str(datos.get("game_id") or "")
        token = str(datos.get("token") or "")
        mensaje = datos.get("mensaje")
        if not isinstance(mensaje, dict):
            self.error_json("Mensaje invalido")
            return
        with CANDADO:
            partida = PARTIDAS.get(game_id)
            if partida is None:
                self.error_json("La partida no existe")
                return
            simbolo = simbolo_por_token(partida, token)
            if simbolo is None:
                self.error_json("Token invalido")
                return
            partida.setdefault("ultima_actividad", {})[simbolo] = time.time()
            procesar_mensaje(partida, simbolo, mensaje)
        self.responder({"ok": True})

    def recibir(self, ruta):
        params = urllib.parse.parse_qs(ruta.query)
        game_id = params.get("game_id", [""])[0]
        token = params.get("token", [""])[0]
        fin = time.time() + INTERVALO_SONDEO_S
        while True:
            with CANDADO:
                partida = PARTIDAS.get(game_id)
                if partida is None:
                    self.error_json("La partida no existe")
                    return
                simbolo = simbolo_por_token(partida, token)
                if simbolo is None:
                    self.error_json("Token invalido")
                    return
                partida.setdefault("ultima_actividad", {})[simbolo] = time.time()
                mensajes = partida["colas"][simbolo][:]
                if mensajes:
                    partida["colas"][simbolo].clear()
                    self.responder({"mensajes": mensajes})
                    return
            if time.time() >= fin:
                self.responder({"mensajes": []})
                return
            time.sleep(0.05)


def crear_servidor_lobby(host="", puerto=None):
    puerto = int(puerto if puerto is not None else os.environ.get("PORT", 8000))
    return http.server.ThreadingHTTPServer((host, puerto), Handler)


def main():
    servidor = crear_servidor_lobby()
    host, puerto = servidor.server_address
    print(f"Lobby online escuchando en {host or '0.0.0.0'}:{puerto}")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()


if __name__ == "__main__":
    main()
