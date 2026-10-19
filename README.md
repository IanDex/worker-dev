# Worker de revisión de Hodor

Tu panel de revisión corre en tu máquina: http://127.0.0.1:7788. Pegas el link de tu PR, lo analizas con tu Claude
(el mismo paquete y las mismas reglas del revisor central), ves el análisis completo, corriges y vuelves a analizar.
Cuando quedas conforme, lo entregas. Si un commit no lo analizas tú, lo revisa el revisor central igual que siempre.

## Requisitos

- Python 3.9 o más nuevo.
- El CLI `claude` instalado y con tu sesión iniciada (corre `claude` una vez y entra con tu cuenta).
- Tu token personal de la revisión v3, que te llega por DM. Es personal: no lo compartas ni lo subas a ningún repo.

## Instalar

```bash
git clone https://github.com/IanDex/worker-dev.git
cd worker-dev
```

1. Copia `config.ejemplo.json` como `config.json` y pega tu token entre las comillas. `config.json` está en el
   `.gitignore`: nunca se sube.
2. Corre el instalador:
   - macOS: `chmod +x instalar.sh && ./instalar.sh`
   - Windows (PowerShell): `powershell -ExecutionPolicy Bypass -File instalar.ps1`
3. Al final se abre tu panel. El worker queda corriendo en segundo plano y arranca solo al iniciar sesión.

La guía completa, con la configuración del plan de pruebas, está en `guias/windows.md` y `guias/mac.md`.

## WSL

Si trabajas dentro de WSL (tu código, git y `claude` viven en Linux), instala todo dentro de WSL con `instalar.sh`:

```bash
git clone https://github.com/IanDex/worker-dev.git
cd worker-dev
cp config.ejemplo.json config.json   # y pega tu token
chmod +x instalar.sh && ./instalar.sh
```

- Python, `claude` (con tu sesión iniciada en WSL) y git corren dentro de WSL.
- Si tu WSL tiene systemd, el worker queda como servicio y arranca solo; si no, arranca cada vez que abres la terminal
  de WSL.
- El panel se abre en el navegador de Windows: http://127.0.0.1:7788.
- «Abrir en terminal con Claude» abre Windows Terminal en tu WSL, dentro del repo del PR.
- En el paso 1 de la configuración, la carpeta de tus repos es la de Linux (por ejemplo `~/code`).

## Actualizar

No tienes que hacer nada: desde la 3.0 el worker revisa cada 30 minutos si hay una versión nueva en este repo. Si la hay,
hace `git pull`, copia los archivos y se reinicia solo (nunca en medio de un análisis). Por eso:

- Deja la carpeta `worker-dev` donde la clonaste: el worker la usa para actualizarse.
- No modifiques sus archivos: Hodor verifica la huella de la versión instalada y rechaza los análisis de un kit que no
  coincide, y con cambios locales el `git pull` no se puede hacer.

Si algo falla al actualizarse o al analizar, el revisor lo ve en su panel.

## Cómo saber que funciona

- `python ~/.hodor-v3/kit/revisor_dev.py --estado` muestra tu cola.
- El log está en `~/.hodor-v3/worker.log`.
