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

## Actualizar

Dentro de la carpeta `worker-dev`:

```bash
git pull
```

Y vuelve a correr el instalador. Reemplaza el código y conserva tu token, tu configuración, tus análisis y tu base local
(`~/.hodor-v3`).

No modifiques los archivos del worker: Hodor verifica la huella de la versión instalada y rechaza los análisis de un
kit que no coincide.

## Cómo saber que funciona

- `python ~/.hodor-v3/kit/revisor_dev.py --estado` muestra tu cola.
- El log está en `~/.hodor-v3/worker.log`.
