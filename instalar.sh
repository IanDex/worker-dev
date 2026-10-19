#!/bin/bash
# Instala el worker v3 de Hodor en macOS (launchd) o en Linux/WSL (systemd de usuario o, sin systemd, al abrir la terminal).
# Copia el kit a ~/.hodor-v3/kit. Actualizar no borra nada: se conserva tu config.json, tus análisis y tu base local.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.hodor-v3/kit"
command -v claude >/dev/null || { echo "Falta el CLI claude: instálalo e inicia sesión (claude) antes de seguir."; exit 1; }
PY="$(command -v python3)"; [ -n "$PY" ] || { echo "Falta python3."; exit 1; }
mkdir -p "$DEST"
"$PY" "$DIR/fusionar_config.py" "$DIR/config.json" "$DEST/config.json" "$DIR"
cp "$DIR/revisor_dev.py" "$DIR/pruebas_dev.py" "$DIR/panel.html" "$DIR/fusionar_config.py" "$DEST/"
# El plan de pruebas lee la BD de ci: necesita pymysql.
"$PY" -c "import pymysql" 2>/dev/null || "$PY" -m pip install --user --quiet pymysql 2>/dev/null \
  || "$PY" -m pip install --user --quiet --break-system-packages pymysql 2>/dev/null \
  || echo "No pude instalar pymysql: el plan de pruebas no podrá leer la BD (en Ubuntu: sudo apt install python3-pymysql)."
"$PY" "$DEST/revisor_dev.py" --estado
RUTA="$(dirname "$(command -v claude)"):/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
PANEL="http://127.0.0.1:7788"

if [ "$(uname)" = "Darwin" ]; then
  PLIST="$HOME/Library/LaunchAgents/com.global66.hodor-v3.plist"
  cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.global66.hodor-v3</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$DEST/revisor_dev.py</string></array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$RUTA</string></dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/.hodor-v3/launchd.log</string>
  <key>StandardErrorPath</key><string>$HOME/.hodor-v3/launchd.log</string>
</dict></plist>
PL
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST"
  sleep 3; open "$PANEL"
  echo "Listo: el worker v3 corre en segundo plano. Tu panel: $PANEL · Log: ~/.hodor-v3/worker.log"
  exit 0
fi

# Linux / WSL
pkill -f "$DEST/revisor_dev.py" 2>/dev/null || true
if [ -d /run/systemd/system ] && systemctl --user show-environment >/dev/null 2>&1; then
  mkdir -p "$HOME/.config/systemd/user"
  cat > "$HOME/.config/systemd/user/hodor-v3.service" <<UN
[Unit]
Description=Worker de revisión v3 de Hodor

[Service]
ExecStart=$PY $DEST/revisor_dev.py
Environment=PATH=$RUTA
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
UN
  systemctl --user daemon-reload
  systemctl --user enable --now hodor-v3.service
  systemctl --user restart hodor-v3.service
  COMO="como servicio de systemd (arranca solo)"
else
  # Sin systemd (WSL por defecto): arranca ahora y cada vez que abres una terminal, si no está corriendo.
  MARCA="# hodor-v3: worker de revisión"
  grep -qF "$MARCA" "$HOME/.profile" 2>/dev/null || cat >> "$HOME/.profile" <<PR

$MARCA
pgrep -f "$DEST/revisor_dev.py" >/dev/null 2>&1 || (PATH="$RUTA:\$PATH" nohup "$PY" "$DEST/revisor_dev.py" >>"$HOME/.hodor-v3/worker.out" 2>&1 &)
PR
  PATH="$RUTA:$PATH" nohup "$PY" "$DEST/revisor_dev.py" >>"$HOME/.hodor-v3/worker.out" 2>&1 &
  COMO="en segundo plano (vuelve a arrancar cada vez que abres la terminal de WSL)"
fi
sleep 3
if command -v cmd.exe >/dev/null 2>&1; then
  (cd /mnt/c && cmd.exe /c start "" "$PANEL") >/dev/null 2>&1 || true
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$PANEL" >/dev/null 2>&1 || true
fi
echo "Listo: el worker v3 corre $COMO. Tu panel: $PANEL (ábrelo en el navegador de Windows) · Log: ~/.hodor-v3/worker.log"
