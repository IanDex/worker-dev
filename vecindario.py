"""
El vecindario de un PR: lo que el revisor iba a buscar a CodeCommit uno por uno, resuelto antes de llamarlo.

Contra la punta de la rama destino del PR (no la de main ni la del dev):
  - dónde está definido cada método o tipo que el diff usa y no define, o que no existe (eso no compila);
  - quién llama a los métodos que el PR agrega o cambia (lo que podría romper en otro archivo);
  - la cabecera de cada clase tocada que el diff no muestra (@RequestMapping, @Service, @FeignClient…).

Usa un espejo local del repo (~/.supervisor/espejos/<repo>.git, solo la punta de cada rama, fetch incremental) y
`git grep`, sin clonar el repo por revisión. Si algo falla, la revisión sigue sin vecindario.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)

ESPEJOS = Path.home() / ".supervisor" / "espejos"
TOPE_SIMBOLOS = 40
TOPE_LLAMADORES = 8
DEF_METODO = re.compile(r"^\s*(?:@\w+(?:\([^)]*\))?\s*)*(?:(?:public|private|protected|static|final|abstract|default|synchronized)\s+)*"
                        r"(?:<[^>]+>\s*)?[\w<>\[\],.? ]+\s+(\w+)\s*\(")
LLAMADA = re.compile(r"(?<![\w.])(?:\w+\.)*?\.?(\w+)\s*\(")
TIPO = re.compile(r"\b([A-Z][A-Za-z0-9]+)\b")
RESERVADAS = {"if", "for", "while", "switch", "catch", "return", "new", "super", "this", "synchronized", "assert", "throw",
              "else", "try", "do", "case", "instanceof"}
COMUNES = {"String", "Long", "Integer", "Boolean", "Double", "List", "Map", "Set", "Optional", "Object", "Override", "Test",
           "BigDecimal", "LocalDate", "LocalDateTime", "Instant", "UUID", "Stream", "Collectors", "Arrays", "Collections",
           "HashMap", "ArrayList", "HashSet", "Exception", "RuntimeException", "Void", "Math", "System", "Objects",
           "Mockito", "Assertions", "ResponseEntity", "HttpStatus", "Slf4j", "Data", "Builder", "Getter", "Setter",
           "Autowired", "Value", "Transactional", "Service", "Component", "Repository", "RestController", "Mock",
           "InjectMocks", "BeforeEach", "DisplayName", "Nested", "ExtendWith", "MockitoExtension", "Spy", "Captor",
           "ArgumentCaptor", "Page", "Pageable", "Sort", "Function", "Supplier", "Consumer", "Predicate", "Logger"}
COMUNES_METODOS = {"get", "set", "of", "equals", "hashCode", "toString", "stream", "map", "filter", "collect", "toList",
                   "orElse", "orElseThrow", "isPresent", "isEmpty", "add", "put", "size", "contains", "builder", "build",
                   "when", "thenReturn", "verify", "assertEquals", "assertThrows", "assertTrue", "assertFalse",
                   "assertNotNull", "assertNull", "any", "eq", "never", "times", "info", "warn", "error", "debug",
                   "valueOf", "format", "getMessage", "forEach", "findById", "save", "saveAll", "findAll", "delete",
                   "orElseGet", "ifPresent", "anyMatch", "allMatch", "noneMatch", "flatMap", "sorted", "distinct",
                   "join", "trim", "length", "substring", "isBlank", "asList", "ok", "status", "body", "now"}


def _git(espejo: Path, *args: str, timeout: int = 60) -> str:
    res = subprocess.run(["git", "-C", str(espejo), "-c", "credential.helper=!aws codecommit credential-helper $@",
                          "-c", "credential.UseHttpPath=true", *args], capture_output=True, text=True, timeout=timeout,
                         encoding="utf-8", errors="replace", creationflags=SIN_VENTANA)
    return res.stdout if res.returncode == 0 else ""


def espejo(repo: str, rama: str) -> tuple[Path, str] | None:
    """El espejo del repo con la punta de `rama` al día. Devuelve (ruta, ref) o None."""
    ruta = ESPEJOS / f"{repo}.git"
    if not ruta.exists():
        ESPEJOS.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "--bare", str(ruta)], capture_output=True, timeout=30, creationflags=SIN_VENTANA)
        url = f"https://git-codecommit.{os.environ.get('AWS_REGION', 'us-east-1')}.amazonaws.com/v1/repos/{repo}"
        subprocess.run(["git", "-C", str(ruta), "remote", "add", "origin", url], capture_output=True, timeout=30, creationflags=SIN_VENTANA)
    ref = f"refs/espejo/{rama}"
    _git(ruta, "fetch", "-q", "--depth", "1", "origin", f"+refs/heads/{rama}:{ref}", timeout=180)
    return (ruta, ref) if _git(ruta, "rev-parse", "--verify", "-q", ref).strip() else None


# Patrones de git grep en ERE POSIX: \b, \s y \w no existen en todas las instalaciones de git.
NO_PAL = "(^|[^A-Za-z0-9_])"
ESP = "[[:space:]]"


def _grep(espejo_: Path, ref: str, patron: str, rutas: str = "*.java", tope: int = 40) -> list:
    salida = _git(espejo_, "grep", "-n", "-E", "-I", "--max-count", "5", patron, ref, "--", rutas)
    filas = []
    for linea in salida.splitlines()[:tope]:
        partes = linea.split(":", 3)
        if len(partes) == 4:
            filas.append((partes[1], partes[2], partes[3].strip()[:160]))
    return filas


def _cambios(diff: str) -> dict:
    """{ruta: (líneas agregadas, líneas quitadas)} del diff unificado."""
    archivos: dict = {}
    actual = None
    for linea in diff.splitlines():
        if linea.startswith("+++ b/"):
            actual = linea[6:]
            archivos[actual] = ([], [])
        elif actual and linea.startswith("+") and not linea.startswith("+++"):
            archivos[actual][0].append(linea[1:])
        elif actual and linea.startswith("-") and not linea.startswith("---"):
            archivos[actual][1].append(linea[1:])
    return archivos


def _definidos(lineas: list) -> set:
    nombres = set()
    for l in lineas:
        m = DEF_METODO.match(l)
        if m and m.group(1) not in RESERVADAS and not l.strip().startswith(("return", "new ", "throw")):
            nombres.add(m.group(1))
    return nombres


def armar(repo: str, rama: str, diff: str, carpeta: Path) -> dict:
    """Escribe `vecindario.md` en la carpeta de la revisión. Devuelve un resumen para medir."""
    hecho = espejo(repo, rama)
    if not hecho:
        return {"ok": False, "motivo": f"no pude traer {rama}"}
    espejo_, ref = hecho
    cambios = {r: v for r, v in _cambios(diff).items() if r.endswith(".java")}
    if not cambios:
        return {"ok": False, "motivo": "sin archivos Java"}
    agregadas = [l for a, _q in cambios.values() for l in a]
    definidos_aqui = set()
    for ruta in cambios:
        archivo = carpeta / "after" / ruta
        if archivo.exists():
            definidos_aqui |= _definidos(archivo.read_text(encoding="utf-8", errors="replace").splitlines())
    llamados = [m for l in agregadas if not l.strip().startswith(("//", "*", "import ")) for m in LLAMADA.findall(l)]
    usados = [n for n in dict.fromkeys(llamados) if n not in definidos_aqui and n not in RESERVADAS
              and n not in COMUNES_METODOS and not n[:1].isupper() and len(n) > 3][:TOPE_SIMBOLOS]
    tipos = [t for t in dict.fromkeys(t for l in agregadas if not l.strip().startswith(("//", "*")) for t in TIPO.findall(l))
             if t not in COMUNES and not t.isupper()][:TOPE_SIMBOLOS]
    cambiados = sorted({n for r, (a, q) in cambios.items() if "/test/" not in r
                        for n in _definidos(a) | _definidos(q) if n not in COMUNES_METODOS})[:TOPE_SIMBOLOS]

    m = [f"# Vecindario del PR contra `{rama}`", "",
         f"Resuelto antes de la revisión sobre la punta de `{rama}` (espejo local, git grep). Úsalo antes de pedir archivos "
         "a CodeCommit; si algo no está aquí, recién ahí usa get-file.", ""]
    faltan = []
    m.append("## Métodos que el diff usa y no define")
    m.append("")
    for nombre in usados:
        defs = [f for f in _grep(espejo_, ref, f"[]A-Za-z0-9_>]{ESP}+{nombre}{ESP}*\\(", tope=12)
                if DEF_METODO.match(f[2]) and not re.search(r"\b(return|new)\b|=", f[2].split(nombre)[0])]
        if defs:
            m.append(f"- `{nombre}(`: " + "; ".join(f"`{r}:{n}` {t}" for r, n, t in defs[:3]))
        elif re.match(r"(get|set|is)[A-Z]", nombre):
            m.append(f"- `{nombre}(`: sin método escrito en `{rama}`; por el nombre, probablemente es un getter/setter de Lombok")
        else:
            faltan.append(nombre)
            m.append(f"- `{nombre}(`: **no encontré su definición en `{rama}`** (puede venir de una librería, de Lombok o "
                     "no existir: verifícalo antes de afirmar que no compila)")
    if not usados:
        m.append("- (ninguno fuera de lo común)")
    m += ["", "## Tipos que el diff usa"]
    m.append("")
    sin_tipo = []
    for tipo in tipos:
        defs = _grep(espejo_, ref, f"(class|interface|enum|record){ESP}+{tipo}([^A-Za-z0-9_]|$)", tope=2)
        if defs:
            m.append(f"- `{tipo}`: `{defs[0][0]}`")
        else:
            sin_tipo.append(tipo)
    if sin_tipo:
        m.append(f"- Sin definición en `{rama}` (librería externa o nuevo en este PR): " + ", ".join(f"`{t}`" for t in sin_tipo))
    m += ["", "## Quién llama a los métodos que el PR agrega o cambia", ""]
    for nombre in cambiados:
        usos = [f for f in _grep(espejo_, ref, f"{NO_PAL}{nombre}{ESP}*\\(", tope=TOPE_LLAMADORES + 4)
                if f[0] not in cambios and not DEF_METODO.match(f[2])][:TOPE_LLAMADORES]
        if usos:
            m.append(f"- `{nombre}(` se usa en: " + "; ".join(f"`{r}:{n}`" for r, n, _t in usos))
        else:
            m.append(f"- `{nombre}(`: nadie más lo usa en `{rama}` fuera de los archivos del PR")
    m += ["", "## Cabecera de las clases tocadas en `" + rama + "`", ""]
    for ruta in cambios:
        texto = _git(espejo_, "show", f"{ref}:{ruta}")
        if not texto:
            m.append(f"- `{ruta}`: archivo nuevo (no existe en `{rama}`)")
            continue
        cab = []
        for l in texto.splitlines():
            if l.strip().startswith("@") or re.match(r"\s*(public\s+)?(abstract\s+)?(class|interface|enum|record)\s", l):
                cab.append(l.strip())
            if re.match(r"\s*(public\s+)?(abstract\s+)?(class|interface|enum|record)\s", l):
                break
        if cab:
            m.append(f"- `{ruta}`: " + " · ".join(f"`{c}`" for c in cab[-6:]))
    (carpeta / "vecindario.md").write_text("\n".join(m) + "\n", encoding="utf-8")
    return {"ok": True, "usados": len(usados), "sin_definicion": faltan, "tipos": len(tipos), "cambiados": len(cambiados)}
