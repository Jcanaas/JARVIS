# JARVIS — app móvil (Expo)

Cliente móvil del asistente de escritorio. Habla con el backend Flask de
`actions/lan_dashboard.py` por la red local (puerto 8765), autenticado con el
token que viaja en el QR de emparejamiento que muestra la app de escritorio.

El audio **nunca** suena en el móvil: mpv lo reproduce en el PC. La app es un
mando a distancia.

## Uso normal (Expo Go)

```bash
npx expo start
```

Escanea el QR de Metro con Expo Go. El emparejamiento con JARVIS es aparte: la
primera vez la app pide escanear el QR que genera el escritorio.

## Controles de música en la notificación / pantalla de bloqueo

**Esto requiere un development build. No funciona en Expo Go.**

Motivo: los controles los provee `expo-media-control`, que es un módulo nativo.
La alternativa sin código nativo (`expo-notifications` con botones) no sirve
aquí: en Android, Expo Go no incluye `TaskManager`, así que los botones solo
responderían con la app en primer plano — justo lo contrario de lo que se busca.

Estado actual: **integración escrita y verificada por tipos, sin compilar
todavía.**

- `src/MediaControlBridge.tsx` — publica la canción actual en la sesión de medios
  del sistema y traduce los botones (play/pausa/anterior/siguiente/seek) en
  llamadas a la API del escritorio.
- Está montado en `App.tsx` solo cuando hay conexión emparejada.
- **En Expo Go el puente no hace nada y la app no casca.** No basta con un
  `try/catch` alrededor del `require`: `expo-media-control` llama a
  `requireNativeModule()` en el ámbito del módulo, así que el import explota
  antes de llegar a cualquier comprobación. El puente pregunta primero por el
  módulo nativo con `requireOptionalNativeModule("ExpoMediaControl")`, que
  devuelve `null` en vez de lanzar, y solo entonces importa el paquete. Se
  puede seguir desarrollando en Expo Go con normalidad.

### Para probarlo (cuando toque)

Ver «Generar el APK» más abajo.

Notas:

- La versión de `expo-media-control` está **fijada** (`1.0.12`, sin `^`) a
  propósito: el repo se declara "under active development" y avisa de cambios
  rompedores entre versiones.
- El plugin añade solo los permisos `FOREGROUND_SERVICE`, `WAKE_LOCK` y
  `ACCESS_NETWORK_STATE` en Android.
- Hay que recompilar el APK cada vez que se añada o actualice una dependencia
  nativa; el código JS sigue recargándose en caliente como siempre.

## El móvil como mando

No hay pestaña de mando a propósito: un mando solo tiene sentido mientras hay
una partida en marcha. `src/components/GamepadOverlay.tsx` consulta
`/api/gamepad/status` cada 5 s y, cuando el escritorio tiene un juego cargado,
avisa por dos vías:

- un **diálogo** dentro de la app, y
- una **notificación persistente** de Android (`sticky`), para poder entrar sin
  tener la app abierta. Al tocarla se abre el mando directamente.

Al tocar «Usar como mando» se abre a pantalla completa, en horizontal y sin
dejar que el móvil se apague. Cuando la partida termina, el mando y la
notificación se cierran solos.

Si descartas el aviso, el botón **«📱 Mando móvil»** del reproductor del
emulador (en el PC) lo vuelve a lanzar: incrementa un contador que el móvil
compara con el último valor visto (`announce` en `/api/gamepad/status`).

El mando **se dibuja según la consola**, no con un diseño único: el backend
publica en `/api/gamepad/status` un `layout` (`actions/emulator_runtime.py`,
`pad_layout()`) que dice cuántos sticks hay, si existen gatillos L2/R2, si el
stick se puede pulsar (L3/R3) y qué glifos usar en los botones frontales. Así
PS2 sale con dos sticks y ✕ ○ □ △, N64 con uno, y una Game Boy solo con cruceta
y dos botones.

Los sticks mandan ejes analógicos (`set_axis`), con un tope de un envío cada
50 ms mientras arrastras — una petición por frame táctil saturaría el enlace —
pero el centrado al soltar sale siempre y sin límite: un stick que se quede
desviado deja al personaje andando solo.

Alcance: funciona con el **emulador integrado** (núcleos libretro que ejecuta
el propio JARVIS), donde controlamos la ruta de entrada vía
`LibretroCore.set_button()` / `set_axis()`. Juegos externos (Steam y compañía)
necesitarían sintetizar un gamepad a nivel de sistema, que es otro problema.

Aviso de iOS: `app.json` fija `"orientation": "portrait"`, así que el bloqueo a
horizontal en tiempo de ejecución funciona en Android pero iOS puede
ignorarlo — allí habría que ampliar las orientaciones soportadas.

## Generar el APK

El proyecto ya está listo para compilar: identificador de app
(`com.jcanadas.jarvis`), `versionCode`, iconos, splash, permisos y `eas.json`
con tres perfiles. Falta únicamente **iniciar sesión con tu cuenta de Expo**,
que tienes que hacer tú.

Dos avisos sobre los comandos:

- Hay que ejecutarlos **dentro de `mobile-app/`**, no en la raíz del repo.
- El paquete se llama `eas-cli` aunque el binario sea `eas`, así que
  `npx eas …` falla con «could not determine executable to run». Usa
  `npx eas-cli@latest …`, o instálalo una vez en global con
  `npm install -g eas-cli` y entonces sí funciona `eas …` a secas.

```bash
cd mobile-app && npx eas-cli@latest login
```

APK instalable directamente en el móvil (el que quieres para uso normal):

```bash
cd mobile-app && npx eas-cli@latest build --profile preview --platform android
```

APK de desarrollo, si quieres seguir recargando código en caliente contra
Metro (necesario para probar los controles de notificación):

```bash
cd mobile-app && npx eas-cli@latest build --profile development --platform android
```

Y luego, con ese build instalado:

```bash
cd mobile-app && npx expo start --dev-client
```

Compilar **en local** no es viable ahora mismo en esta máquina: no hay SDK de
Android ni Android Studio, y el Java instalado es 25 (Gradle de React Native
espera JDK 17). El build en la nube de EAS evita todo eso.

## Parche a expo-media-control (no borrar)

`patches/expo-media-control+1.0.12.patch` añade dos líneas a la notificación de
Android: `setColor()` y `setColorized(true)`. La librería acepta `color` y
`colorized` en sus tipos de TypeScript pero **nunca los aplicaba** en Android,
así que la notificación salía siempre con el fondo por defecto del sistema.

El color lo calcula el PC (`cover_accent_color()` en `actions/lan_dashboard.py`)
cuantizando la carátula y quedándose con el tono dominante que aún tenga algo de
saturación — la media saldría gris y el cubo más grande suele ser el borde negro
de la portada. Viaja como `coverColor` en `/api/status`.

Se reaplica solo gracias al `postinstall` (`patch-package`), también en los
builds de EAS. Si algún día se actualiza `expo-media-control`, el parche fallará
ruidosamente y habrá que regenerarlo.

## HTTP en claro (no quitar)

`expo-build-properties` fija `android.usesCleartextTraffic: true` a propósito.
Desde Android 9 una app compilada no puede hacer peticiones `http://`, y toda
la comunicación con el PC es HTTP plano contra una IP de LAN. En Expo Go no se
nota (Expo Go ya lo trae permitido en su manifiesto): **solo falla en el APK**,
y el síntoma es «no se pudo conectar» justo después de escanear el QR, con el
PC funcionando perfectamente.

Ojo: `android.usesCleartextTraffic` suelto en `app.json` **no vale** — no está
en el esquema de SDK 57 y `expo-doctor` lo rechaza. Tiene que ir por el plugin.

## Verificación

```bash
npx tsc --noEmit
```

```bash
npx expo-doctor
```
