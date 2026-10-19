"""
Plan de pruebas en tu máquina, con tus credenciales de ci: tus usuarios de prueba, tu usuario de la BD (en sesión de solo lectura) y
tus llaves de backoffice. Nada de eso sale de tu máquina: los secretos van al llavero del sistema (Keychain en macOS,
DPAPI en Windows) y Hodor solo recibe el plan, el log y el dictamen.

Lo usa el worker (revisor_dev.py) y, como herramientas, el Claude que arma y dicta el plan:

    python pruebas_dev.py usuarios                       tus usuarios de prueba con su empresa (sin contraseñas)
    python pruebas_dev.py pedir <rol|id|-|admin> "<ruta>" ['<query json>']   un GET de exploración en ci
    python pruebas_dev.py sql "<SELECT ...>"             una lectura acotada de la BD de ci
    python pruebas_dev.py git <repo> <show|grep|log|ls-tree> ...   el código de tu clon del repo
"""
from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

AQUI = Path(__file__).resolve().parent
TRABAJO = Path(os.environ.get("HODOR_V3_TRABAJO") or (Path.home() / ".hodor-v3")).expanduser()
DB = TRABAJO / "hodor.db"
AMBIENTE = "ci"
GATEWAY = "https://ci-api.global66.com"
LB = "https://lb-ci.global66.com"
ORIGEN_B2B = "https://empresas-test.global66.com"
RUTA_LOGIN = "/auth-server/b2b/auth/login"
RUTA_PRUEBA_BO = "/company/bo/kyc/status"
TOPE_CUERPO = 200_000
TOPE_CUERPO_LOG = 4000
SQL_FILAS, SQL_TOPE, SQL_MS = 50, 200, 5000
SERVICIO = "hodor-v3"
_CANDADO = threading.Lock()
_TOKENS: dict = {}


# ── Base local y secretos ──

def _db() -> sqlite3.Connection:
    TRABAJO.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS ajustes (clave TEXT PRIMARY KEY, valor TEXT);
        CREATE TABLE IF NOT EXISTS usuarios_prueba (id TEXT PRIMARY KEY, email TEXT NOT NULL, alias TEXT, empresa TEXT,
                                                    company_id TEXT, nivel TEXT, pais TEXT, kyc TEXT, probado REAL, error TEXT);
        CREATE TABLE IF NOT EXISTS secretos (clave TEXT PRIMARY KEY, valor TEXT);
        CREATE TABLE IF NOT EXISTS planes (id INTEGER PRIMARY KEY AUTOINCREMENT, fila INTEGER, pr TEXT, repo TEXT, commit_pr TEXT,
                                           estado TEXT, plan TEXT, ejecucion TEXT, dictamen TEXT, chat TEXT, sesion TEXT,
                                           error TEXT, creado REAL, actualizado REAL, enviado INTEGER DEFAULT 0);
    """)
    return con


def ajuste(clave: str, defecto: str = "") -> str:
    with _CANDADO, _db() as con:
        fila = con.execute("SELECT valor FROM ajustes WHERE clave = ?", (clave,)).fetchone()
    return fila["valor"] if fila and fila["valor"] is not None else defecto


def poner_ajuste(clave: str, valor: str) -> None:
    with _CANDADO, _db() as con:
        con.execute("INSERT INTO ajustes(clave, valor) VALUES(?, ?) ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor", (clave, valor))


def _dpapi(datos: bytes, cifrar: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    entrada = Blob(len(datos), ctypes.cast(ctypes.create_string_buffer(datos, len(datos)), ctypes.POINTER(ctypes.c_char)))
    salida = Blob()
    funcion = ctypes.windll.crypt32.CryptProtectData if cifrar else ctypes.windll.crypt32.CryptUnprotectData
    if not funcion(ctypes.byref(entrada), None, None, None, None, 0, ctypes.byref(salida)):
        raise OSError("DPAPI no pudo procesar el secreto")
    try:
        return ctypes.string_at(salida.pbData, salida.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(salida.pbData)


def guardar_secreto(clave: str, valor: str) -> None:
    """macOS: Keychain. Windows: cifrado con DPAPI (solo tu usuario de Windows lo descifra). Otro sistema: la base local."""
    if sys.platform == "darwin":
        subprocess.run(["security", "add-generic-password", "-U", "-s", SERVICIO, "-a", clave, "-w", valor],
                       check=True, capture_output=True, timeout=20)
        guardado = "keychain"
    elif sys.platform.startswith("win"):
        guardado = "dpapi:" + base64.b64encode(_dpapi(valor.encode("utf-8"), True)).decode()
    else:
        guardado = "plano:" + valor
    with _CANDADO, _db() as con:
        con.execute("INSERT INTO secretos(clave, valor) VALUES(?, ?) ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor", (clave, guardado))


def leer_secreto(clave: str) -> str:
    with _CANDADO, _db() as con:
        fila = con.execute("SELECT valor FROM secretos WHERE clave = ?", (clave,)).fetchone()
    if not fila:
        return ""
    valor = fila["valor"] or ""
    if valor == "keychain":
        r = subprocess.run(["security", "find-generic-password", "-s", SERVICIO, "-a", clave, "-w"], capture_output=True, text=True, timeout=20)
        return r.stdout.rstrip("\n") if r.returncode == 0 else ""
    if valor.startswith("dpapi:"):
        return _dpapi(base64.b64decode(valor[6:]), False).decode("utf-8")
    return valor[6:] if valor.startswith("plano:") else ""


def hay_secreto(clave: str) -> bool:
    with _CANDADO, _db() as con:
        return bool(con.execute("SELECT 1 FROM secretos WHERE clave = ?", (clave,)).fetchone())


def borrar_secreto(clave: str) -> None:
    if sys.platform == "darwin":
        subprocess.run(["security", "delete-generic-password", "-s", SERVICIO, "-a", clave], capture_output=True, timeout=20)
    with _CANDADO, _db() as con:
        con.execute("DELETE FROM secretos WHERE clave = ?", (clave,))


def tapar(texto: str) -> str:
    return re.sub(r"eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{5,}", "[token]", str(texto))


# ── HTTP ──

def _pedir(metodo: str, url: str, cabeceras: dict, cuerpo: bytes | None, espera: float = 30) -> dict:
    pedido = urllib.request.Request(url, data=cuerpo, method=metodo, headers=cabeceras)
    inicio = time.time()
    try:
        with urllib.request.urlopen(pedido, timeout=espera) as r:
            status, datos = r.status, r.read(TOPE_CUERPO + 1)
    except urllib.error.HTTPError as err:
        status, datos = err.code, err.read(TOPE_CUERPO + 1)
    except (urllib.error.URLError, OSError) as err:
        return {"error": f"no respondió ({getattr(err, 'reason', err)}): ¿VPN, dominio o servicio caído?", "ms": int((time.time() - inicio) * 1000)}
    return {"status": status, "ms": int((time.time() - inicio) * 1000), "cuerpo": datos[:TOPE_CUERPO].decode("utf-8", "replace")}


def _jwt(token: str) -> dict:
    try:
        cuerpo = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(cuerpo + "=" * (-len(cuerpo) % 4)))
    except (IndexError, ValueError):
        return {}


# ── Usuarios de prueba ──

def login(email: str, password: str, otp: str = "") -> dict:
    res = _pedir("POST", GATEWAY + RUTA_LOGIN, {"content-type": "application/json", "authtype": "WHATSAPP", "authvalue": otp,
                                               "device": "desktop", "platform": "mobile", "fingerprint": "hodor-v3", "origin": ORIGEN_B2B},
                 json.dumps({"email": email, "password": password, "useOtp": True}).encode())
    if res.get("status") == 200:
        try:
            token = json.loads(res["cuerpo"]).get("idToken") or ""
        except ValueError:
            token = ""
        if token:
            return {"status": 200, "token": token}
        return {"status": 200, "error": "entró pero pide el código OTP: agrégalo al usuario"}
    return {"status": res.get("status"), "error": (res.get("cuerpo") or res.get("error") or "")[:300]}


def kyc(token: str) -> dict:
    res = _pedir("GET", GATEWAY + "/company/b2b/kyc/status", {"authorization": token, "accept": "application/json", "x-api-version": "2",
                                                              "origin": ORIGEN_B2B}, None)
    try:
        d = json.loads(res.get("cuerpo") or "{}")
    except ValueError:
        return {}
    u, c = d.get("b2bUser") or {}, d.get("company") or {}
    return {"nombre": " ".join(x for x in (u.get("name"), u.get("lastName")) if x), "nivel": u.get("level"),
            "rep_legal": u.get("isLegalRepresentative"), "empresa": c.get("name"), "company_id": c.get("id"),
            "pais": (c.get("addressCountry") or {}).get("country")}


def usuarios() -> list:
    with _CANDADO, _db() as con:
        return [dict(r) for r in con.execute("SELECT id, email, alias, empresa, company_id, nivel, pais, probado, error FROM usuarios_prueba ORDER BY id")]


def guardar_usuario(email: str, password: str, uid: str = "", otp: str = "") -> dict:
    """Prueba el login y, si entra, guarda al usuario con su empresa; la contraseña va al llavero."""
    email = email.strip().lower()
    if "@" not in email:
        return {"error": "falta el correo"}
    actual = next((u for u in usuarios() if u["id"] == uid or u["email"] == email), None)
    uid = (actual or {}).get("id") or f"u{int(time.time() * 1000):x}"
    password = password or (leer_secreto(f"usuario:{uid}") if actual else "")
    if not password:
        return {"error": "falta la contraseña"}
    otp = otp.strip() or (leer_secreto(f"otp:{uid}") if actual else "")
    r = login(email, password, otp)
    if r.get("status") != 200 or not r.get("token"):
        return {"error": r.get("error") if r.get("status") == 200 else f"no entra en ci ({r.get('status') or r.get('error')}): revisa correo, contraseña y VPN"}
    k = kyc(r["token"])
    guardar_secreto(f"usuario:{uid}", password)
    if otp:
        guardar_secreto(f"otp:{uid}", otp)
    alias = " · ".join(x for x in (k.get("empresa"), k.get("nombre") or email.split("@")[0]) if x)
    with _CANDADO, _db() as con:
        con.execute("""INSERT INTO usuarios_prueba(id, email, alias, empresa, company_id, nivel, pais, kyc, probado, error)
                       VALUES(?,?,?,?,?,?,?,?,?,NULL) ON CONFLICT(id) DO UPDATE SET email=excluded.email, alias=excluded.alias,
                       empresa=excluded.empresa, company_id=excluded.company_id, nivel=excluded.nivel, pais=excluded.pais,
                       kyc=excluded.kyc, probado=excluded.probado, error=NULL""",
                    (uid, email, alias[:80], k.get("empresa") or "", str(k.get("company_id") or ""), str(k.get("nivel") or ""),
                     k.get("pais") or "", json.dumps(k, ensure_ascii=False), time.time()))
    _TOKENS[uid] = (r["token"], _jwt(r["token"]).get("exp") or time.time() + 2700)
    return {"ok": True, "id": uid, "empresa": k.get("empresa"), "company_id": k.get("company_id"), "nivel": k.get("nivel"), "pais": k.get("pais")}


def borrar_usuario(uid: str) -> dict:
    borrar_secreto(f"usuario:{uid}")
    borrar_secreto(f"otp:{uid}")
    with _CANDADO, _db() as con:
        con.execute("DELETE FROM usuarios_prueba WHERE id = ?", (uid,))
    _TOKENS.pop(uid, None)
    return {"ok": True}


def _token(uid: str) -> str:
    guardado = _TOKENS.get(uid)
    if guardado and time.time() < float(guardado[1]) - 120:
        return guardado[0]
    u = next((x for x in usuarios() if x["id"] == uid), None)
    if not u:
        raise ValueError(f"no existe el usuario de prueba {uid}")
    r = login(u["email"], leer_secreto(f"usuario:{uid}"), leer_secreto(f"otp:{uid}"))
    if r.get("status") != 200 or not r.get("token"):
        with _CANDADO, _db() as con:
            con.execute("UPDATE usuarios_prueba SET error = ? WHERE id = ?", (f"login {r.get('status')}", uid))
        raise ValueError(f"el login de {u['alias'] or u['email']} falló ({r.get('status') or r.get('error')})")
    _TOKENS[uid] = (r["token"], _jwt(r["token"]).get("exp") or time.time() + 2700)
    return r["token"]


# ── Backoffice y BD ──

def guardar_bo(ktk: str, at: str) -> dict:
    if ktk:
        guardar_secreto("bo:ktk", ktk.strip())
    if at:
        guardar_secreto("bo:at", at.strip())
    return probar_bo()


def probar_bo() -> dict:
    if not (hay_secreto("bo:ktk") and hay_secreto("bo:at")):
        return {"error": "faltan las dos llaves"}
    res = _pedir("GET", LB + RUTA_PRUEBA_BO, {"accept": "application/json", "KNOWN-TOKEN-KEY": leer_secreto("bo:ktk"),
                                              "ADMIN-TOKEN-KEY": leer_secreto("bo:at")}, None)
    if res.get("error"):
        return {"error": res["error"]}
    if res.get("status") in (401, 403):
        return {"error": f"el backoffice de ci rechazó las llaves ({res['status']})"}
    poner_ajuste("bo_probado", str(time.time()))
    return {"ok": True, "status": res.get("status")}


def guardar_bd(host: str, puerto: str, usuario: str, password: str) -> dict:
    poner_ajuste("bd_host", host.strip())
    poner_ajuste("bd_puerto", str(puerto or "3306").strip())
    poner_ajuste("bd_usuario", usuario.strip())
    if password:
        guardar_secreto("bd:password", password)
    return probar_bd()


ESCRITURA = re.compile(r"\b(ALL PRIVILEGES|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|INDEX|GRANT OPTION|EXECUTE|TRIGGER)\b", re.I)


def _conectar():
    try:
        import pymysql
    except ImportError as err:
        raise RuntimeError("falta pymysql: corre `python -m pip install --user pymysql`") from err
    host, usuario = ajuste("bd_host"), ajuste("bd_usuario")
    if not (host and usuario and hay_secreto("bd:password")):
        raise RuntimeError("configura tu usuario de la BD de ci")
    try:
        return pymysql.connect(host=host, port=int(ajuste("bd_puerto", "3306") or 3306), user=usuario, password=leer_secreto("bd:password"),
                               connect_timeout=6, read_timeout=30, charset="utf8mb4")
    except pymysql.err.OperationalError as err:
        raise RuntimeError(f"no llego a la BD de ci: ¿VPN prendida? ({str(err)[:120]})") from err


def probar_bd() -> dict:
    """Conecta y hace SELECT 1. Un usuario con permiso de escritura se acepta: el plan solo manda SELECT, SHOW, DESCRIBE
    o EXPLAIN (acotar_sql) y cada consulta corre en una sesión READ ONLY, así que MySQL rechaza cualquier escritura."""
    try:
        con = _conectar()
    except RuntimeError as err:
        return {"error": str(err)}
    try:
        with con.cursor() as cur:
            cur.execute("SELECT 1")
            cur.execute("SHOW GRANTS")
            permisos = [str(f[0]) for f in cur.fetchall()]
    finally:
        con.close()
    escritura = any(ESCRITURA.search(m.group(1)) and not re.search(r"information_schema|performance_schema", m.group(2), re.I)
                    for m in (re.match(r"\s*GRANT\s+(.+?)\s+ON\s+(\S+)", p, re.I) for p in permisos) if m)
    poner_ajuste("bd_probado", str(time.time()))
    poner_ajuste("bd_escritura", "1" if escritura else "")
    return {"ok": True, "escritura": escritura}


def acotar_sql(consulta: str) -> tuple:
    q = consulta.strip().rstrip(";").strip()
    if ";" in q:
        return "", "una sola sentencia"
    primera = q.split(None, 1)[0].upper() if q else ""
    if primera in ("SHOW", "DESCRIBE", "DESC", "EXPLAIN"):
        return q, ""
    if primera not in ("SELECT", "WITH"):
        return "", "solo SELECT, SHOW, DESCRIBE o EXPLAIN"
    plano = re.sub(r"\s+", " ", q.upper())
    if re.search(r"SELECT \* FROM [\w.`]+( AS \w+| \w+)?( LIMIT| ORDER| GROUP|$)", plano) and " WHERE " not in plano:
        return "", "SELECT * sin WHERE trae la tabla entera: pide las columnas que necesitas y filtra por id, empresa o usuario"
    tope = re.search(r"\bLIMIT\s+(\d+)(\s*,\s*(\d+))?\s*$", q, re.I)
    if tope:
        if int(tope.group(3) or tope.group(1)) > SQL_TOPE:
            q = q[:tope.start()] + f"LIMIT {SQL_TOPE}"
    else:
        q += f" LIMIT {SQL_FILAS}"
    if primera == "SELECT" and "MAX_EXECUTION_TIME" not in plano:
        q = re.sub(r"^\s*SELECT", f"SELECT /*+ MAX_EXECUTION_TIME({SQL_MS}) */", q, count=1, flags=re.I)
    return q, ""


def sql_filas(consulta: str) -> tuple:
    consulta, error = acotar_sql(consulta)
    if error:
        return [], f"consulta rechazada: {error}"
    try:
        con = _conectar()
    except RuntimeError as err:
        return [], str(err)
    try:
        with con.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute(consulta)
            columnas = [d[0] for d in cur.description or []]
            filas = [{c: (v if isinstance(v, (int, float, str)) or v is None else str(v)) for c, v in zip(columnas, f)} for f in cur.fetchmany(SQL_TOPE)]
    except Exception as err:  # noqa: BLE001 — el error de MySQL es la respuesta
        return [], str(err)[:300]
    finally:
        con.close()
    return filas, ""


# ── Pedidos a ci ──

def ejecutar(pedido: dict) -> dict:
    """{metodo, ruta, query, headers, cuerpo, usuario (id), via: gateway|lb|admin} → respuesta. Solo ci."""
    ruta = "/" + str(pedido.get("ruta") or "").lstrip("/")
    if "://" in ruta or ".." in ruta:
        raise ValueError("la ruta debe ser relativa al ambiente")
    query = {k: v for k, v in (pedido.get("query") or {}).items() if v not in (None, "")}
    via = (pedido.get("via") or ("lb" if "/iuse/" in ruta else "gateway")).lower()
    admin = via == "admin"
    url = (LB if via in ("lb", "admin") else GATEWAY) + ruta + ("?" + urllib.parse.urlencode(query) if query else "")
    cabeceras = {"content-type": "application/json", "accept": "application/json"}
    cabeceras.update({str(k): str(v) for k, v in (pedido.get("headers") or {}).items() if v not in (None, "")})
    uid = pedido.get("usuario")
    if admin:
        if not (hay_secreto("bo:ktk") and hay_secreto("bo:at")):
            raise ValueError("faltan tus llaves de backoffice de ci (Configuración)")
        cabeceras.update({"KNOWN-TOKEN-KEY": leer_secreto("bo:ktk"), "ADMIN-TOKEN-KEY": leer_secreto("bo:at")})
    if uid and via in ("lb", "admin"):
        datos = _jwt(_token(uid))
        cabeceras = {**{k: str(v) for k, v in {"Claim-Customer-Id": datos.get("custom:customer_id"), "Claim-B2B-User-Id": datos.get("custom:b2b_user_id"),
                                               "Claim-Email": datos.get("email")}.items() if v}, **cabeceras}
    elif uid:
        cabeceras.setdefault("authorization", _token(uid))
        cabeceras.setdefault("device", "desktop")
        cabeceras.setdefault("platform", "mobile")
        cabeceras.setdefault("origin", ORIGEN_B2B)
    metodo = str(pedido.get("metodo") or "GET").upper()
    cuerpo = pedido.get("cuerpo")
    datos_b = None
    if cuerpo not in (None, "", {}) and metodo not in ("GET", "HEAD"):
        datos_b = (cuerpo if isinstance(cuerpo, str) else json.dumps(cuerpo, ensure_ascii=False)).encode()
    salida = _pedir(metodo, url, cabeceras, datos_b)
    if uid and via == "gateway" and salida.get("status") == 401:
        _TOKENS.pop(uid, None)
        cabeceras["authorization"] = _token(uid)
        salida = {**_pedir(metodo, url, cabeceras, datos_b), "reintento": "401: volvió a entrar y lo reintentó"}
    secretas = {"authorization", "known-token-key", "admin-token-key"}
    visibles = {k: ("•••" if k.lower() in secretas else v) for k, v in cabeceras.items()}
    return {**salida, "url": url, "metodo": metodo, "enviadas": visibles, "via": "admin" if admin else via}


# ── Ejecutar un plan (sin Claude) ──

_FALTA = object()


def _leer(obj, ruta: str):
    actual = obj
    for parte in [p for p in str(ruta).split(".") if p]:
        if isinstance(actual, list):
            if parte.isdigit() and int(parte) < len(actual):
                actual = actual[int(parte)]
                continue
            actual = actual[0] if actual else None
        if not isinstance(actual, dict) or parte not in actual:
            return _FALTA
        actual = actual[parte]
    return actual


def _cumple(valor, regla: dict) -> bool:
    if "existe" in regla:
        return (valor is not _FALTA) == bool(regla["existe"])
    if "no_existe" in regla:
        return valor is _FALTA
    if valor is _FALTA:
        return False
    if "igual" in regla:
        return valor == regla["igual"] or str(valor) == str(regla["igual"])
    if "distinto" in regla:
        return valor != regla["distinto"]
    if "mayor" in regla:
        return isinstance(valor, (int, float)) and valor > regla["mayor"]
    if "menor" in regla:
        return isinstance(valor, (int, float)) and valor < regla["menor"]
    if "contiene" in regla:
        return str(regla["contiene"]).lower() in json.dumps(valor, ensure_ascii=False).lower()
    return True


def _sustituir(valor, variables: dict):
    if isinstance(valor, str):
        completo = re.fullmatch(r"\{\{\s*([\w.$-]+)\s*\}\}", valor)
        if completo and completo.group(1) in variables:
            return variables[completo.group(1)]
        return re.sub(r"\{\{\s*([\w.$-]+)\s*\}\}", lambda m: str(variables.get(m.group(1), m.group(0))), valor)
    if isinstance(valor, dict):
        return {k: _sustituir(v, variables) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_sustituir(v, variables) for v in valor]
    return valor


def _paso_sql(paso: dict, variables: dict) -> dict:
    consulta = _sustituir(paso.get("sql") or "", variables)
    filas, error = sql_filas(consulta)
    espera, checks = paso.get("espera") or {}, []
    if espera.get("filas") is not None:
        checks.append({"que": "filas", "espera": espera["filas"], "obtuvo": len(filas), "ok": len(filas) == espera["filas"]})
    for campo, regla in (espera.get("campos") or {}).items():
        valor = _leer(filas, campo)
        checks.append({"que": campo, "espera": regla, "obtuvo": None if valor is _FALTA else valor,
                       "ok": _cumple(valor, regla if isinstance(regla, dict) else {"igual": regla})})
    for var, campo in (paso.get("guardar") or {}).items():
        valor = _leer(filas, campo)
        if valor is not _FALTA:
            variables[var] = valor
    return {"nombre": paso.get("nombre"), "tipo": paso.get("tipo") or "verificacion", "metodo": "SQL", "url": consulta[:500], "status": None,
            "error": error or None, "respuesta": tapar(json.dumps(filas[:20], ensure_ascii=False, default=str))[:TOPE_CUERPO_LOG],
            "checks": checks, "ok": not error and all(c["ok"] for c in checks)}


def ejecutar_escenario(esc: dict, base: dict, roles: dict) -> dict:
    reg = {"id": esc.get("id"), "nombre": esc.get("nombre"), "pasos": [], "resultado": "pasa", "en": time.time()}
    if not esc.get("ejecutable", True):
        return {**reg, "resultado": "no_ejecutable", "motivo": esc.get("motivo")}
    variables = {**base, "$uuid": str(uuid.uuid4()), "$timestamp": str(int(time.time()))}
    for paso in esc.get("pasos") or []:
        if paso.get("sql"):
            reg["pasos"].append(_paso_sql(paso, variables))
            if not reg["pasos"][-1]["ok"]:
                reg["resultado"] = "falla"
            continue
        rol = paso.get("usuario")
        uid = roles.get(rol) if rol else None
        if rol and not uid:
            reg["pasos"].append({"nombre": paso.get("nombre"), "error": f"falta el usuario {rol}"})
            reg["resultado"] = "no_preparado"
            break
        pedido = {"metodo": paso.get("metodo") or "GET", "ruta": _sustituir(paso.get("ruta") or "/", variables),
                  "query": _sustituir(paso.get("query") or {}, variables), "headers": _sustituir(paso.get("headers") or {}, variables),
                  "cuerpo": _sustituir(paso.get("cuerpo"), variables), "usuario": uid, "via": paso.get("via")}
        try:
            res = ejecutar(pedido)
        except ValueError as err:
            res = {"error": str(err)}
        try:
            cuerpo = json.loads(res.get("cuerpo") or "null")
        except ValueError:
            cuerpo = res.get("cuerpo")
        espera, checks = paso.get("espera") or {}, []
        if espera.get("status") is not None:
            checks.append({"que": "status", "espera": espera["status"], "obtuvo": res.get("status"), "ok": res.get("status") == espera["status"]})
        for campo, regla in (espera.get("campos") or {}).items():
            valor = _leer(cuerpo, campo)
            checks.append({"que": campo, "espera": regla, "obtuvo": None if valor is _FALTA else valor,
                           "ok": _cumple(valor, regla if isinstance(regla, dict) else {"igual": regla})})
        for var, campo in (paso.get("guardar") or {}).items():
            valor = _leer(cuerpo, campo)
            if valor is not _FALTA:
                variables[var] = valor
        ok = not res.get("error") and all(c["ok"] for c in checks)
        reg["pasos"].append({"nombre": paso.get("nombre"), "tipo": paso.get("tipo"), "usuario": rol, "metodo": pedido["metodo"],
                             "url": res.get("url") or pedido["ruta"],
                             "enviado": tapar(json.dumps(pedido["cuerpo"], ensure_ascii=False))[:2000] if pedido["cuerpo"] is not None else None,
                             "status": res.get("status"), "ms": res.get("ms"), "error": res.get("error"), "reintento": res.get("reintento"),
                             "via": res.get("via"), "cabeceras": {k: tapar(str(v))[:200] for k, v in (res.get("enviadas") or {}).items()},
                             "respuesta": tapar(str(res.get("cuerpo") or ""))[:TOPE_CUERPO_LOG], "checks": checks, "ok": ok})
        if not ok:
            reg["resultado"] = "no_preparado" if paso.get("tipo") == "preparacion" else "falla"
            if paso.get("tipo") == "preparacion":
                break
    return reg


def ejecutar_plan(plan: dict, solo: list | None = None, anterior: dict | None = None) -> dict:
    """Corre los escenarios ejecutables (o solo los de `solo`, conservando del log `anterior` los demás)."""
    roles = {u.get("rol"): u.get("id") for u in plan.get("usuarios") or [] if u.get("rol")}
    base = {x.get("nombre"): x.get("valor") for x in plan.get("datos") or [] if x.get("nombre")}
    previos = {e.get("id"): e for e in (anterior or {}).get("escenarios") or []}
    log = {"ambiente": AMBIENTE, "inicio": time.time(), "usuarios": roles, "escenarios": []}
    for esc in plan.get("escenarios") or []:
        if solo and esc.get("id") not in solo and esc.get("id") in previos:
            log["escenarios"].append(previos[esc.get("id")])
            continue
        log["escenarios"].append(ejecutar_escenario(esc, base, roles))
    log["fin"] = time.time()
    return log


def cambios_permitidos(antes: dict, despues: dict) -> str:
    """Al corregir datos del plan en el chat se puede cambiar cómo se prueba (ruta, vía, cabeceras, query, cuerpo, datos,
    usuarios), nunca qué se espera: los mismos escenarios, con el mismo tipo, severidad y "espera" en cada paso.
    Vacío si el cambio es válido; si no, el motivo."""
    a = {e.get("id"): e for e in antes.get("escenarios") or []}
    d = {e.get("id"): e for e in despues.get("escenarios") or []}
    if set(a) - set(d):
        return "no se puede quitar un escenario: " + ", ".join(sorted(set(a) - set(d)))
    for eid, ea in a.items():
        ed = d[eid]
        for campo in ("tipo", "si_falla", "objetivo"):
            if ea.get(campo) != ed.get(campo):
                return f"{eid}: no se puede cambiar {campo}"
        if ea.get("ejecutable", True) and not ed.get("ejecutable", True):
            return f"{eid}: no se puede marcar como no ejecutable"
        esperas_a = [p.get("espera") for p in ea.get("pasos") or [] if p.get("tipo") != "preparacion"]
        esperas_d = [p.get("espera") for p in ed.get("pasos") or [] if p.get("tipo") != "preparacion"]
        if esperas_a != esperas_d:
            return f"{eid}: lo que se espera de la prueba no se cambia desde el chat"
    return ""


# ── git sobre tu clon ──

def _repo(nombre: str) -> Path:
    base = Path(ajuste("repos") or os.environ.get("HODOR_V3_REPOS") or (Path.home() / "Documents")).expanduser()
    for c in (base / nombre, base / nombre.lower()):
        if (c / ".git").exists():
            return c
    raise ValueError(f"no encuentro {nombre} en {base}")


def cli_git(repo: str, *args: str) -> None:
    if not args or args[0] not in ("show", "grep", "log", "ls-tree"):
        print(json.dumps({"error": "solo show, grep, log o ls-tree"}))
        return
    carpeta = _repo(repo)
    marca = TRABAJO / f"fetch-{repo}.marca"
    if not marca.exists() or time.time() - marca.stat().st_mtime > 600:
        subprocess.run(["git", "-C", str(carpeta), "fetch", "--quiet", "origin", "master", "development", "release"],
                       capture_output=True, timeout=120)
        marca.touch()
    r = subprocess.run(["git", "-C", str(carpeta), *args], capture_output=True, text=True, timeout=60)
    print((r.stdout or r.stderr)[:40000])


# ── CLI para Claude ──

def cli_usuarios() -> None:
    salida = []
    for u in usuarios():
        fila = {k: u[k] for k in ("id", "alias", "email", "empresa", "company_id", "nivel", "pais") if u.get(k)}
        try:
            fila["token"] = {k: v for k, v in _jwt(_token(u["id"])).items() if k.startswith("custom:") or k == "email"}
        except Exception as err:  # noqa: BLE001
            fila["error_login"] = str(err)[:200]
        salida.append(fila)
    print(json.dumps(salida, ensure_ascii=False, default=str))


def cli_pedir(usuario: str, ruta: str, query: str = "") -> None:
    admin = usuario == "admin"
    pedido = {"metodo": "GET", "ruta": ruta, "query": json.loads(query) if query else {},
              "usuario": None if usuario in ("-", "", "admin") else usuario, **({"via": "admin"} if admin else {})}
    try:
        res = ejecutar(pedido)
    except ValueError as err:
        print(json.dumps({"error": str(err)}, ensure_ascii=False))
        return
    print(tapar(json.dumps({"status": res.get("status"), "ms": res.get("ms"), "cuerpo": (res.get("cuerpo") or "")[:6000],
                            "error": res.get("error")}, ensure_ascii=False)))


def cli_sql(consulta: str) -> None:
    filas, error = sql_filas(consulta)
    print(json.dumps({"error": error} if error else {"filas": filas}, ensure_ascii=False, default=str)[:20000])


def herramientas(python: str) -> list:
    script = Path(__file__).resolve().as_posix()
    cmds = ("usuarios", "pedir", "sql", "git")
    return ["Read", "Grep", "Glob", *[f"Bash({python} {script} {c}:*)" for c in cmds], *[f'Bash("{python}" "{script}" {c}:*)' for c in cmds]]


PROHIBIDO = ["Write", "Edit", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch", "Task", "Read(~/.hodor-v3/**)", "Read(~/.aws/**)",
             "Read(~/.ssh/**)", "Read(~/.claude/**)", "Read(~/.config/**)", "Read(**/.env)", "Read(**/.env.*)", "Read(**/*.pem)",
             "Read(**/*credentials*)", "Read(**/*secret*)", "Read(**/config.json)"]


def main() -> int:
    args = sys.argv[1:]
    try:
        if args[:1] == ["usuarios"]:
            cli_usuarios()
        elif args[:1] == ["pedir"] and len(args) >= 3:
            cli_pedir(args[1], args[2], args[3] if len(args) > 3 else "")
        elif args[:1] == ["sql"] and len(args) == 2:
            cli_sql(args[1])
        elif args[:1] == ["git"] and len(args) >= 3:
            cli_git(*args[1:])
        else:
            print(__doc__)
            return 1
    except Exception as err:  # noqa: BLE001 — Claude lee el error y sigue
        print(json.dumps({"error": str(err)[:300]}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
