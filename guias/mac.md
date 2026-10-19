# Hodor v3 · Tu worker de revisión (macOS)

{nombre}, desde hoy revisas tus PRs tú mismo, en tu máquina y con tu Claude, y entregas al revisor solo lo que ya
quedó bien.

## Cómo funciona

Desde ahora analizas tus PRs en tu máquina antes de que los revise Hodor. Tu worker trae un **panel** en tu navegador:
**http://127.0.0.1:7788** (se abre al instalar; después, `revisor_dev.py --panel`).

1. Abres tu PR como siempre: los avisos de Sonar crean la tarjeta y Hodor lo toma con las reglas de siempre. Si tú
   ya analizaste ese commit, Hodor usa tu análisis; si no, lo revisa el revisor central.
2. En tu panel pegas el link del PR y pulsas **Analizar**. Hodor arma el paquete (diff exacto, lineamientos y las
   mismas instrucciones del revisor central) y tu worker lo analiza con tu `claude`.
3. Ves el análisis completo, agrupado por historia: puntaje, hallazgos con archivo y línea, qué hace el cambio,
   escenarios de prueba, preguntas y orden de despliegue.
4. **Abrir en terminal con Claude** abre una terminal en tu repo con Claude leyendo ese análisis, para corregir
   conversando. Haces push y vuelves a **Analizar** las veces que necesites.
5. Cuando quedas conforme, pulsas **Entregar al revisor**: Hodor toma tu análisis y pasa el PR
   por las reglas de siempre (historia, master después de development, Sonar, conflictos). Si el análisis es del
   commit actual, Hodor actúa con él: en development lo aprueba si no hay nada que bloquee; en master le llega al
   revisor con tu sello. Si hiciste push después de analizar, te pide analizar de nuevo.

Todo PR necesita su historia en la rama o en el título (`usuario/dev/HU-123`). Algunas entregas se vuelven a revisar
con el revisor central para comparar (auditoría al azar). Con críticas abiertas no se puede entregar.

## Lo que necesitas

- **Python 3.9 o más nuevo.** En Terminal, `python3 --version` debe mostrar la versión. Si no lo tienes:
  `brew install python` o el instalador de python.org.
- **El CLI `claude`** con tu sesión iniciada: abre Terminal, corre `claude` una vez y entra con tu cuenta.
- **Tu token personal.** Te llega en un mensaje aparte de Hodor. Es tuyo: no lo compartas ni lo subas a ningún repo.

## Instalación

1. Descomprime `hodor-v3-worker.zip`, por ejemplo en `~/hodor-v3`.
2. En esa carpeta, copia `config.ejemplo.json` como `config.json`. Ábrelo y reemplaza
   `PEGA-AQUI-EL-TOKEN-QUE-TE-DIO-CRISTIAN` por tu token, entre las comillas:

   ```json
   {"api": "https://b2b.global66.tech", "token": "tu-token-aquí"}
   ```

3. En Terminal, dentro de esa carpeta:

   ```bash
   chmod +x instalar.sh && ./instalar.sh
   ```

4. Al final debes ver algo como esto (tu correo y tu cola, que puede estar vacía):

   ```
   {"dev": "{correo}", "pendientes": []}
   Listo: el worker v3 corre en segundo plano.
   ```

El instalador copia el worker a `~/.hodor-v3/kit` y lo deja como servicio de launchd
(`com.global66.hodor-v3`), que arranca solo al iniciar sesión y se reinicia si se cae. No tienes que dejar ninguna
Terminal abierta.

## Cómo saber que funciona

- Ver tu cola: `python3 ~/.hodor-v3/kit/revisor_dev.py --estado`
- Ver el log: `tail -f ~/.hodor-v3/worker.log`
- Ver el servicio: `launchctl list | grep hodor-v3`

Cuando entra un PR, el log muestra `bajando el paquete` y, al terminar, `listo en N s ... Hodor lo recibió`.

## Plan de pruebas en ci (PRs a release)

Un PR a release se entrega solo con su plan de pruebas en ci aprobado y sin ningún IDOR. El plan lo arma y lo ejecuta
tu worker, con tus credenciales. Configúralas una vez en el panel (botón del engranaje, pasos 3 a 5):

1. **Base de datos de ci:** tu usuario de la BD de ci. Con la VPN prendida, pega host, puerto, usuario y contraseña y
   toca *Probar y guardar*. El plan solo lee: cada consulta corre en una sesión de solo lectura.
2. **Usuarios de prueba de ci:** tus usuarios B2B de prueba, de **dos empresas distintas** (así se prueba que un usuario
   no ve datos de otra empresa). Pega correo, contraseña y el código OTP de ci de cada uno.
3. **Backoffice de ci:** tu KNOWN-TOKEN-KEY y tu ADMIN-TOKEN-KEY.

Las contraseñas y llaves van al llavero de tu sistema y nunca salen de tu máquina. Si un escenario falla por un dato
del plan (ruta, vía, cabecera, id), corrígelo en el chat de la sección de pruebas; lo que se espera de cada escenario
no se cambia.

## Si algo falla

| Síntoma | Qué hacer |
|---|---|
| `token inválido` | Revisa que el token esté completo en `config.json` (43 caracteres) y vuelve a correr el instalador. |
| `Falta el CLI claude` | Instala Claude Code, corre `claude` una vez para iniciar sesión y reinstala. |
| `Falta python3` | `brew install python` y reinstala. |
| El log no avanza | `launchctl kickstart -k gui/$(id -u)/com.global66.hodor-v3` y revisa `~/.hodor-v3/launchd.log`. |

Para desinstalar: `launchctl unload ~/Library/LaunchAgents/com.global66.hodor-v3.plist`, borra ese archivo y
`~/.hodor-v3`.

Cualquier duda, escríbele al revisor.
