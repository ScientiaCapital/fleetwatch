<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/banner-dark.svg">
    <img alt="Fleetwatch for Epiphan Edge" src="docs/assets/banner-light.svg" width="720">
  </picture>
</p>

<p align="center">
  <a href="https://scientiacapital.github.io/fleetwatch/">Documentación (en inglés)</a> ·
  <a href="#instalación">Instalación</a> ·

  <a href="SECURITY.md">Seguridad</a> ·
  <a href="README.md">English</a>
</p>

# Fleetwatch for Epiphan Edge

**Un vigilante de solo lectura, siempre activo, para tu flota de Epiphan Edge.** Revisa cada sala en cada ciclo,
publica un resumen tranquilo en Slack cuando algo cambia y dice **Ready** (listo) o **Not ready** (no listo)
30 minutos antes de cada evento programado.

La versión 0.1 **solo observa**. No puede cambiar ningún equipo: las herramientas de escritura se rechazan dentro
del cliente antes de que salga cualquier petición, y `policy.yaml` queda fijado en `autonomy: observe`.

<p align="center">
  <img alt="Un resumen de Fleetwatch y dos revisiones antes de un evento, de la demo sin conexión" src="docs/assets/digest-replay.svg" width="720">
</p>

<p align="center"><sub>De <code>fleetwatch digest --replay tests/fixtures</code>: una flota de ejemplo, sin datos reales. Los mensajes están en inglés.</sub></p>

## Qué incluye

| | |
|---|---|
| **Resumen tranquilo** | Publica solo cuando algo cambia. Cada problema se publica una vez, se recuerda como máximo cada 4 horas y se cierra con *Back to normal*. |
| **Ready / Not ready** | 30 minutos antes de cada evento, una línea por sala: ¿hay imagen?, ¿el equipo está en línea? |
| **Solo lectura por diseño** | Las herramientas de escritura se rechazan dentro del cliente, antes de que salga ninguna petición. |
| **Funciona en una Pi o una Mac mini** | Instalación en una línea como servicio systemd o launchd, o Docker en amd64 y arm64. |
| **`fleetwatch doctor`** | Una línea por revisión: política, protección, ocultación de secretos, inicio de sesión, red, servicio. |
| **Demo sin conexión** | Un ciclo completo contra una flota de ejemplo guardada. Sin cuenta y sin red. |

## Instalación

macOS o Linux, en una línea. Instala [uv](https://docs.astral.sh/uv/) si hace falta, inicia sesión en Epiphan Edge
e instala el servicio. Puedes [leer el script](install.sh) antes.

```bash
curl -fsSL https://raw.githubusercontent.com/ScientiaCapital/fleetwatch/main/install.sh | bash
```

¿Prefieres Docker? `docker compose run --rm fleetwatch login` y luego `docker compose up -d`.

## Inicio rápido

¿Sin cuenta a mano? Ejecuta un ciclo completo contra la muestra guardada:

```bash
git clone https://github.com/ScientiaCapital/fleetwatch.git && cd fleetwatch
uv sync
uv run fleetwatch digest --replay tests/fixtures
```

Con tu propio equipo:

```bash
cp .env.example .env            # token del bot de Slack y canal; sin token, imprime en la consola
uv run fleetwatch login         # inicio de sesión único en Epiphan Edge
uv run fleetwatch digest        # un ciclo: imprime o publica el resumen
uv run fleetwatch run           # sigue ejecutándose, cada 3 minutos
uv run fleetwatch doctor        # ¿está lista esta máquina?
```

Las cuentas de Europa o Australia ponen `FLEETWATCH_EPIPHAN_MCP_URL` en `eu.` o `au.epiphan.cloud`.

## Solución de problemas

Empieza con `fleetwatch doctor`. No inicia sesión ni llama herramientas, así que es seguro ejecutarlo en cualquier
lugar. Cada `WARN` o `FAIL` indica el comando que lo arregla.

| Síntoma | Solución |
|---|---|
| `Sign-in` en WARN o FAIL | Ejecuta `fleetwatch login`. En una Pi sin pantalla, abre el enlace en otro equipo y pega la URL final de `localhost`. |
| `Epiphan reachable` falla | Revisa la red o ajusta `FLEETWATCH_EPIPHAN_MCP_URL` a tu región. |
| No llega nada a Slack | Sin token solo imprime en la consola. Define `FLEETWATCH_SLACK_BOT_TOKEN` (`chat:write`) e invita al bot al canal. |
| Sin red | `fleetwatch digest --replay tests/fixtures` ejecuta el ciclo completo sin conexión. |

La documentación completa está en inglés: [scientiacapital.github.io/fleetwatch](https://scientiacapital.github.io/fleetwatch/).

## Licencia

Copyright 2026 Epiphan Systems Inc. Licencia [Apache-2.0](LICENSE); las atribuciones están en [NOTICE](NOTICE).

Fleetwatch no es un producto con soporte oficial de Epiphan (not an officially supported Epiphan product).
Epiphan, Epiphan Edge, Pearl y EC20 son marcas comerciales de Epiphan Systems Inc.
