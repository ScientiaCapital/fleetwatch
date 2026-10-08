<p align="right"><a href="SECURITY.md">English</a> · Español</p>

# Seguridad

Esta es una versión corta en español. La política completa, incluido cómo reportar una vulnerabilidad, está en
[SECURITY.md](SECURITY.md) (en inglés), y esa versión es la que cuenta si hay diferencias.

Fleetwatch vigila equipos de video de aulas y estudios, así que un error que filtre una clave de transmisión o que le
permita cambiar un dispositivo importa. Gracias por reportarlo en privado.

## Reportar una vulnerabilidad

No abras un issue público. Repórtalo en GitHub: Security, Report a vulnerability en este repositorio
([enlace directo](https://github.com/ScientiaCapital/fleetwatch/security/advisories/new)). Los plazos y los créditos
están en [SECURITY.md](SECURITY.md#report-a-vulnerability).

Los problemas en los productos de Epiphan, en Epiphan Edge o en el servidor MCP de Epiphan le corresponden a Epiphan.
Contacta al [soporte de Epiphan](https://www.epiphan.com/support/).

## Resumen del modelo de confianza

Fleetwatch corre como un proceso en una máquina que controlas, con un inicio de sesión de Epiphan Edge para un equipo.
No confía en nada que venga de Epiphan: los nombres de dispositivos, canales y fuentes, y los títulos de eventos se
tratan como datos, nunca como instrucciones. El cliente rechaza en el código cualquier herramienta que no esté en la
lista `read` de `tool_policy.yaml`, y todo resultado pasa por `redact()` antes de analizarse, guardarse, registrarse o
publicarse.

Epiphan Edge no tiene un inicio de sesión de solo lectura: el token puede hacer lo que la cuenta de Edge puede hacer en
ese equipo. Solo lectura es una promesa del código de Fleetwatch, no de la credencial. Usa una cuenta con el mínimo
acceso y ejecuta Fleetwatch con su propio usuario en una máquina de confianza.

## Modelo de confianza de la v0.2

Esto cubre el asistente opcional y el flujo de aprobación. Están construidos, pero sin publicar, y probados solo con
simulaciones: un servidor de Epiphan simulado y un modelo simulado. Nada de esto se ha ejecutado con un equipo real ni
con la API real de Anthropic. La versión 0.1 no usa nada de esto. El diseño está en
[docs/design/approved-writes.md](docs/design/approved-writes.md) (en inglés).

Lo que el modelo puede ver. Tu pregunta y los resultados, con los secretos ocultos, de las herramientas de lectura que
pide. Esos resultados incluyen nombres de equipos, canales, fuentes y eventos, modelos, grupos, firmware y el estado
de conexión, grabación y eventos. Se envían a la API de Anthropic. La ocultación quita las formas de secreto que
conoce, no todos los secretos posibles; ver los límites conocidos más abajo.

Lo que el modelo puede hacer. Leer, con la misma protección `guard()` que todo lo demás, y llamar a
`propose_change`. Esa herramienta guarda una propuesta pendiente y no ejecuta nada. El modelo nunca ve una
herramienta de escritura. No es de confianza: un texto que lo convenza de proponer algo igual pasa por cada control
de abajo.

Lo que aprueba una persona. Un cambio a la vez, en la página de aprobación (`fleetwatch approve --serve`, ver
[Approving changes](docs/approving-changes.md), en inglés). El código arma la tarjeta con la propuesta guardada y una
lectura nueva de cada destino: la herramienta en palabras simples, el ID, el nombre y el estado de cada dispositivo, y
cada argumento completo. El motivo del asistente aparece en un cuadro aparte, marcado como no verificado. Si un
argumento no se puede mostrar completo, solo se ofrece Deny.

Lo que impide que un cambio se ejecute sin esa aprobación:

- La aprobación sirve una sola vez y vence a los cinco minutos. Queda ligada a la herramienta, los argumentos y los
  destinos exactos, así que un argumento cambiado o una aprobación reutilizada se rechaza.
- La escritura pasa solo por un ejecutor aparte, nunca por el cliente normal. El ejecutor recibe solo una aprobación
  ya consumida y vuelve a leer cada destino justo antes de la llamada. Se niega si una lectura falla o si el estado
  del destino cambió. Llama a la herramienta una vez y nunca reintenta, así que un resultado desconocido se muestra
  como "puede que se haya ejecutado o no".
- Cerco de pruebas (sandbox). La escritura usa un segundo inicio de sesión, hecho con `fleetwatch login --sandbox`, y
  cada destino debe estar en la lista de dispositivos recién leída de ese inicio de sesión. Sin inicio de sesión de
  pruebas, no se puede ejecutar ninguna escritura. El ciclo de revisión y el asistente de solo lectura nunca cargan el
  token de pruebas.
- Las herramientas disruptivas (reinicio, actualización de firmware, preset de equipo, detener o borrar un destino de
  transmisión, borrar un evento y las herramientas aún sin revisar) se rechazan cerca de un evento programado o
  mientras un destino graba, incluso con aprobación.

La página de aprobación:

- Escucha solo en `127.0.0.1`, en su propio puerto, y revisa el encabezado Host.
- Un secreto de página, que se imprime una vez en la consola y nunca va en una URL, la protege. Cinco intentos
  fallidos bloquean el formulario por un minuto.
- Cada POST necesita un token ligado a la propuesta y un encabezado de mismo origen. Nada cambia con un GET.
- Approve nunca es el botón con el foco, y el asistente no puede definir etiquetas, colores ni foco.

Límites conocidos:

- Los navegadores envían las cookies a todos los puertos del mismo host. Otro servidor web local en `localhost`
  podría recibir la cookie de sesión de la página. Usa `127.0.0.1` y no navegues a otros servidores web locales
  mientras la página esté abierta.
- La página sugiere un descanso tras cinco aprobaciones en una sesión. Es una advertencia, no un límite estricto, y
  una persona puede seguir aprobando. El registro de auditoría cuenta las aprobaciones para que se note un patrón de
  aprobar sin leer.
- El OAuth de Epiphan no tiene un alcance de solo lectura. El token del inicio de sesión de pruebas puede escribir en
  el equipo de pruebas, y el token del inicio de sesión normal puede escribir en su equipo. El cerco es código de
  Fleetwatch, no la credencial. Si alguien controla el proceso o sus archivos, tiene un token que puede escribir.
- Si Epiphan devuelve un ID de equipo, y si cada herramienta proponible acepta los argumentos de `tool_policy.yaml`,
  sigue sin confirmarse hasta la primera ejecución real. Hoy la verificación del equipo de pruebas es la lista de
  dispositivos y la lista de permitidos `FLEETWATCH_WRITE_DEVICE_IDS`. Sin esa lista y sin `FLEETWATCH_WRITE_TEAM_ID`,
  ningún cambio se ejecuta. `login --sandbox` se rechaza, y el inicio de sesión se olvida, si el equipo de pruebas
  ve un dispositivo que el inicio de sesión normal ya vigila.
- El modelo puede escribir un motivo o una respuesta engañosos. Lee la tarjeta, no el motivo.

## Más detalles

El almacenamiento del token, el endurecimiento del servicio, el alcance y los demás límites conocidos de la versión
0.1 están en [SECURITY.md](SECURITY.md#how-thats-enforced-in-v01), en inglés.
