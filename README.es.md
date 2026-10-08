<p align="right"><a href="README.md">English</a> · Español</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/banner-dark.svg">
    <img alt="Fleetwatch for Epiphan Edge" src="docs/assets/banner-light.svg" width="720">
  </picture>
</p>

<p align="center">
  <a href="https://scientiacapital.github.io/fleetwatch/">Documentación (en inglés)</a> ·
  <a href="#instalación">Instalación</a> ·
  <a href="SECURITY.md">Seguridad</a>
</p>

# Fleetwatch for Epiphan Edge

Un vigilante de solo lectura, siempre activo, para tu flota de Epiphan Edge. Revisa cada sala en cada ciclo,
publica un resumen tranquilo en Slack o Microsoft Teams cuando algo cambia y dice Ready (listo) o Not ready
(no listo) 30 minutos antes de cada evento programado.

La versión 0.1 solo observa. No puede cambiar ningún equipo: el cliente rechaza las herramientas de escritura antes
de que salga cualquier solicitud, y `policy.yaml` solo acepta `autonomy: observe`.

<p align="center">
  <img alt="Un resumen de Fleetwatch y dos revisiones antes de un evento, de la demo sin conexión" src="docs/assets/digest-replay.svg" width="720">
</p>

<p align="center"><sub>De <code>fleetwatch digest --replay tests/fixtures</code>: una flota de ejemplo, sin datos reales. Los mensajes están en inglés.</sub></p>

## Qué incluye

| | |
|---|---|
| Resumen tranquilo | Publica solo cuando algo cambia. Cada problema se publica una vez, se recuerda como máximo cada cuatro horas y se cierra con Back to normal. |
| Ready o Not ready | 30 minutos antes de cada evento, una línea por sala: Ready, Ready with notes o Not ready. ¿Hay imagen?, ¿el equipo está en línea? Si cambia antes de que empiece, se vuelve a publicar. |
| Solo lectura por diseño | El cliente rechaza las herramientas de escritura antes de que salga cualquier solicitud. Epiphan Edge no tiene un inicio de sesión de solo lectura, así que usa una cuenta con el mínimo acceso. |
| Hecho para una Pi o una Mac mini | Instalación en una línea como servicio systemd o launchd, o con Docker. |
| `fleetwatch doctor` | Una línea por revisión: versión, política, protección, ocultación de secretos, inicio de sesión, carpeta de estado, Slack y Teams, red y servicio. |
| Demo sin conexión | Un ciclo completo contra una flota de ejemplo guardada, o una tranquila para una pantalla en una sala silenciosa. Sin cuenta y sin red. |

## Instalación

macOS o Linux, en una línea. Instala [uv](https://docs.astral.sh/uv/) si hace falta, inicia sesión en Epiphan Edge
e instala el servicio siempre activo. Puedes [leer el script](install.sh) antes.

```bash
curl -fsSL https://raw.githubusercontent.com/ScientiaCapital/fleetwatch/main/install.sh | bash
```

Instala la última versión publicada (mientras no exista la primera, la rama `main`); agrega `-s -- --ref main` para
la rama de desarrollo.

¿Prefieres Docker? Desde un clon del repositorio, `docker compose run --rm fleetwatch login` y luego
`docker compose up -d`. Mientras no exista la primera versión, Compose construye la imagen localmente.

| Nivel | Plataforma | Se ejecuta como |
|---|---|---|
| 1 | macOS en Apple Silicon, como una Mac mini | agente de launchd |
| 1 | Raspberry Pi 5 / Linux aarch64 | unidad de usuario de systemd |
| 1 | Docker | contenedor (`compose.yaml`) |
| 2 | Linux x86_64, macOS en Intel | systemd / launchd |

En cada pull request, la integración continua (CI) ejecuta las pruebas y la instalación de prueba en equipos macOS
(Apple Silicon) y Linux aarch64 de GitHub, ejecuta las pruebas con Python 3.12 y 3.13, y construye y ejecuta la
imagen de Docker para amd64 y para arm64 (arm64 con emulación). Todavía no se ha probado en una Raspberry Pi 5 ni en
una Mac mini reales. Guías paso a paso (en inglés):
[Mac mini](https://scientiacapital.github.io/fleetwatch/mac-mini/),
[Raspberry Pi](https://scientiacapital.github.io/fleetwatch/raspberry-pi/),
[Docker](https://scientiacapital.github.io/fleetwatch/docker/).

## Actualizar

Vuelve a ejecutar la línea de instalación. Lleva `~/fleetwatch` a la última versión publicada, instala sus
dependencias y reinicia el servicio. Tu `.env`, tu inicio de sesión y tu historial no se tocan: el estado en SQLite
vive en `~/.fleetwatch`, y el token de inicio de sesión en el Llavero de macOS o en esa misma carpeta, todo fuera de
la carpeta del código.

```bash
curl -fsSL https://raw.githubusercontent.com/ScientiaCapital/fleetwatch/main/install.sh | bash
```

Para elegir una versión, agrega `-s -- --ref v0.1.0` (cualquier etiqueta de versión, o `main` para la rama de
desarrollo). Si clonaste el repositorio tú mismo, ejecuta `git pull && uv sync` en su carpeta, y luego
`deploy/install.sh` para reiniciar el servicio.

En Docker, `docker compose pull && docker compose up -d` descarga la imagen más reciente de GitHub Container
Registry y reinicia el contenedor. El volumen `fleetwatch-state` conserva el inicio de sesión y el historial. Para
quedarte en una versión, cambia `image:` en `compose.yaml` a una etiqueta de versión, como
`ghcr.io/scientiacapital/fleetwatch:0.1.0`, o `:0.1` para recibir sus correcciones. Mientras no exista la primera
versión no hay imagen que descargar, así que ejecuta `docker compose up -d --build`.

Después, `fleetwatch doctor` muestra la versión en su primera línea. Cada versión en
[GitHub Releases](https://github.com/ScientiaCapital/fleetwatch/releases) dice qué cambió.

## Inicio rápido

¿Sin cuenta a la mano? Ejecuta un ciclo completo contra una muestra guardada de una flota, sin secretos:

```bash
git clone https://github.com/ScientiaCapital/fleetwatch.git && cd fleetwatch
uv sync
uv run fleetwatch digest --replay tests/fixtures
uv run fleetwatch digest --replay tests/fixtures/calm   # una flota tranquila: All clear y una revisión Ready
```

Con tu propio equipo:

```bash
cp .env.example .env            # token del bot de Slack y canal; sin token, imprime en la consola
uv run fleetwatch login         # inicio de sesión único en Epiphan Edge; elige el equipo que quieres vigilar
uv run fleetwatch digest        # un ciclo: imprime o publica el resumen
uv run fleetwatch digest --capture ~/fleetwatch-capture   # además guarda lo que leyó, sin secretos, como muestra de replay
uv run fleetwatch run           # sigue ejecutándose, cada tres minutos
uv run fleetwatch status        # ¿sesión iniciada? ¿pendientes abiertos?
uv run fleetwatch doctor        # ¿está lista esta máquina? una línea por revisión
uv run fleetwatch ask "¿Está lista Courtroom?"   # pregunta en palabras simples; --serve abre una página con botones
uv run fleetwatch history       # una línea por día: tamaño de la flota, en línea, publicaciones, revisión nocturna
uv run fleetwatch note "Room 204 Pearl Mini" "Lámpara cambiada"   # una nota que aparece bajo esa sala
uv run fleetwatch notes --search lámpara                          # ver notas: todas, de una sala o por texto
deploy/install.sh               # ejecútalo como servicio: launchd en macOS, systemd en Linux
```

En una Pi sin pantalla, abre el enlace de inicio de sesión en cualquier equipo. Si la página final de `127.0.0.1` no
carga, pega su URL en la terminal. Las cuentas de Europa o Australia ponen `FLEETWATCH_EPIPHAN_MCP_URL` en
`https://eu.epiphan.cloud/mcp` o `https://au.epiphan.cloud/mcp`.

Así se ve un resumen (los mensajes están en inglés):

```text
*Fleet check*
• *Fix first*: Room 312 Pearl Mini is offline. Events in that room won't record or stream until it's back.
• *Fix soon*: Hall A Auditorium runs firmware 4.24.5; others like it run 4.24.6. Works fine today; keeps the fleet consistent.
• *Fix soon*: Room 312 EC20 is offline. Its picture may be missing from the Pearl channels that use it, and Edge can't control it.
FYI: 3 Pearls have little or no local space left. That's normal when recordings upload to your CMS.
```

- El mismo problema se publica una vez, se recuerda como máximo cada cuatro horas y se cierra con Back to normal.
- Las horas de silencio (de 22:00 a 06:30 por defecto) solo dejan pasar los avisos Fix first y las revisiones antes
  de cada evento.
- Antes de cada evento: `Ballroom B · Opening keynote at 9:00 AM: Ready, with notes`. Si el resultado cambia antes
  de que empiece, una línea más lo dice: `Now not ready (was Ready)` o `Ready now (was Not ready)`.
- Pon `vertical: education`, `business`, `courts` o `worship` en `policy.yaml` y cambian las palabras: class,
  meeting, hearing, service.

## Cómo funciona

Cada ciclo lee la flota por medio del servidor de Model Context Protocol (MCP) de Epiphan y luego ejecuta código
simple: revisiones fijas, una comparación contra SQLite y un mensaje con plantilla. No hace ninguna llamada a un
modelo de lenguaje grande (LLM). Solo usa las herramientas de lectura de `tool_policy.yaml`, y todo lo que lee pasa
por la ocultación de secretos antes de guardarse o publicarse.

## Solución de problemas

Empieza con `fleetwatch doctor`. No inicia sesión ni llama herramientas, así que es seguro ejecutarlo en cualquier
lugar:

```text
OK    Version            fleetwatch 0.1.0, Python 3.12.4 on arm64
OK    Policy             observe-only, heartbeat every 180 s
OK    Read-only guard    20 read tools allowed; every write tool is refused
OK    Redaction          stream keys and credentialed URLs are masked
WARN  Sign-in            not signed in: run  fleetwatch login
OK    State folder       /Users/you/.fleetwatch (created on first run)
OK    Slack              no token: prints to the console
OK    Slack commands     off (set FLEETWATCH_SLACK_APP_TOKEN to answer /fleetwatch)
OK    Teams              not configured
OK    Epiphan reachable  go.epiphan.cloud
WARN  Service            launchd agent not installed: run  deploy/install.sh

Nothing broken. 2 to look at.
```

| Síntoma | Solución |
|---|---|
| `Sign-in` en WARN o FAIL | Ejecuta `fleetwatch login`. En una Pi sin pantalla, abre el enlace en otro equipo y pega la URL final de `127.0.0.1`. |
| `Epiphan reachable` falla | Revisa la red o ajusta `FLEETWATCH_EPIPHAN_MCP_URL` a tu región (`https://eu.epiphan.cloud/mcp` o `https://au.epiphan.cloud/mcp`). |
| No llega nada a Slack | Sin token solo imprime en la consola. Define `FLEETWATCH_SLACK_BOT_TOKEN` (`chat:write`) e invita al bot al canal. |
| Un resumen nunca se repite | Así está pensado. Un problema abierto se recuerda como máximo cada cuatro horas. `fleetwatch status` muestra los pendientes abiertos. |
| Sin red | `fleetwatch digest --replay tests/fixtures` ejecuta el ciclo completo sin conexión; `tests/fixtures/calm` es una flota tranquila. |

¿Sigues atorado? Abre un [issue](https://github.com/ScientiaCapital/fleetwatch/issues) con la salida de `doctor`.

## Seguridad

Fleetwatch solo lee. No escucha en ningún puerto de red salvo `127.0.0.1`, y solo durante `fleetwatch login` o
mientras ejecutas `fleetwatch ask --serve`. Solo se conecta hacia afuera, a tu región de Epiphan y a Slack o Teams.
Oculta los secretos de cada resultado antes de procesarlo, guardarlo, registrarlo o publicarlo. Trata los nombres de
equipos y eventos como datos no confiables. El token de OAuth se renueva solo y se guarda en el Llavero de macOS,
cifrado con `systemd-creds` o en un archivo con permisos `600`.

Epiphan Edge no tiene un inicio de sesión de solo lectura: el token puede hacer todo lo que la cuenta de Edge puede
hacer en ese equipo de trabajo. Fleetwatch nunca usa ese poder, porque la protección rechaza toda herramienta de
escritura, pero un token robado sí podría. Así que inicia sesión con una cuenta dedicada que tenga el mínimo acceso
que aún vea las salas que vigilas, y trata el token como una contraseña. `fleetwatch doctor` te lo recuerda siempre
que hay una sesión guardada.

El [modelo de confianza](SECURITY.md#trust-model), qué está dentro y fuera del alcance y los límites conocidos están
en [SECURITY.md](SECURITY.md) (en inglés). Reporta vulnerabilidades en privado por medio de los
[avisos de seguridad de GitHub](https://github.com/ScientiaCapital/fleetwatch/security/advisories/new).

## Documentación

La documentación completa está en inglés: [scientiacapital.github.io/fleetwatch](https://scientiacapital.github.io/fleetwatch/).

## Contribuir y comunidad

Lo que más ayuda son reportes de errores con una muestra de replay sin secretos (`fleetwatch digest --capture DIR`
crea una), nuevas revisiones y casos de ocultación de secretos. Lee [CONTRIBUTING.md](CONTRIBUTING.md); los
asistentes de IA deben leer [AGENTS.md](AGENTS.md). Las preguntas e ideas van en
[GitHub Discussions](https://github.com/ScientiaCapital/fleetwatch/discussions), y todos siguen el
[Código de conducta](CODE_OF_CONDUCT.md).

## Estado

Versión 0.1.0, todavía sin publicar. Las pruebas unitarias y de replay pasan. Hasta ahora Fleetwatch solo se ha
ejecutado contra la flota de ejemplo guardada: falta la primera ejecución contra un equipo real. Lo que sigue está
en los hitos [Sprint 2](https://github.com/ScientiaCapital/fleetwatch/milestone/1) y
[Sprint 3](https://github.com/ScientiaCapital/fleetwatch/milestone/2), incluida una interfaz de voz que aún no
existe. Las versiones futuras planean agregar propuestas con aprobación en Slack y luego correcciones de rutina por
su cuenta; la protección, la ejecución en seco y la ocultación de secretos se quedan.

## Agradecimientos

Muchas gracias al equipo de ingeniería de Epiphan por construir Epiphan Edge y el servidor MCP de Epiphan.
Fleetwatch se apoya por completo en lo que construyeron, y solo va a seguir mejorando.

## Licencia

Copyright 2026 Epiphan Systems Inc. Licencia [Apache-2.0](LICENSE); las atribuciones están en [NOTICE](NOTICE).
Construido a partir del [Epiphan Edge Claude Kit](https://github.com/ScientiaCapital/epiphan-edge-claude-kit) (MIT).

Fleetwatch no es un producto con soporte oficial de Epiphan (not an officially supported Epiphan product).
Epiphan, Epiphan Edge, Pearl y EC20 son marcas comerciales de Epiphan Systems Inc.
