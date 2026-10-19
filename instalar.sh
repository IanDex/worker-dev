#!/bin/bash
# Instala el worker v3 de Hodor en macOS: copia el kit a ~/.hodor-v3/kit y lo deja corriendo con launchd.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.hodor-v3/kit"
command -v claude >/dev/null || { echo "Falta el CLI claude: instálalo e inicia sesión (claude) antes de seguir."; exit 1; }
PY="$(command -v python3)"; [ -n "$PY" ] || { echo "Falta python3."; exit 1; }
# Actualizar no borra nada: se reemplaza el código y se conserva tu config.json, tus análisis y tu base local (~/.hodor-v3).
mkdir -p "$DEST"
"$PY" "$DIR/fusionar_config.py" "$DIR/config.json" "$DEST/config.json"
cp "$DIR/revisor_dev.py" "$DIR/pruebas_dev.py" "$DIR/panel.html" "$DIR/fusionar_config.py" "$DEST/"
# El plan de pruebas lee la BD de ci con tu usuario de solo lectura: necesita pymysql.
"$PY" -m pip install --user --quiet pymysql || echo "No pude instalar pymysql: el plan de pruebas no podrá leer la BD (pip install --user pymysql)."
"$PY" "$DEST/revisor_dev.py" --estado
PLIST="$HOME/Library/LaunchAgents/com.global66.hodor-v3.plist"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.global66.hodor-v3</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$DEST/revisor_dev.py</string></array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$(dirname "$(command -v claude)"):/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string></dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/.hodor-v3/launchd.log</string>
  <key>StandardErrorPath</key><string>$HOME/.hodor-v3/launchd.log</string>
</dict></plist>
PL
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
sleep 3; open "http://127.0.0.1:7788"
echo "Listo: el worker v3 corre en segundo plano. Tu panel: http://127.0.0.1:7788 · Log: ~/.hodor-v3/worker.log"
