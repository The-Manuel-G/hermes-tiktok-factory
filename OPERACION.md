# MANUAL DE OPERACIÓN — HERMES AGENT TIKTOK FACTORY & FARM

## 1. Cómo Añadir Nuevos Proxies
- Abre el Dashboard en la pestaña **5. Proxies** y pega tus proxies en formato `Host:Puerto:Usuario:Contraseña`, o edita la sección `proxies.lista` en `config.yaml` desde la pestaña **7. Config**.
- El sistema respeta estrictamente **1 proxy sticky = 1 cuenta activa**. Si tienes 10 proxies, podrás operar 10 cuentas simultáneas sin compartir IP.

## 2. Cómo Cambiar la Geolocalización (Geo)
- En la pestaña **2. Fábrica** selecciona el país (`US`, `ES`, `GB`, `DE`, `PL`) o cambia `identidad.pais_objetivo` en `config.yaml`.
- Hermes Agent ajusta automáticamente `Timezone`, `Locale`, `Accept-Language` y `Ciudad` de la biografía para que coincidan con el proxy.

## 3. Cómo Reaccionar ante un Baneo o "Demasiados Intentos"
- Si TikTok responde con *"Too many attempts / Demasiados intentos"*, `health_worker` pausa esa cuenta durante **24 horas exactas**. No fuerces reintentos manuales antes de que se cumpla el plazo.
- Si un proxy acumula fallos o captchas repetidos, entra a la pestaña **3. Cuentas** y pulsa **Rotar IP** para asignarle un proxy nuevo libre.

## 4. Cómo Escalar a Más Cuentas
- Añade más proxies residenciales o dedicados en la pestaña **5. Proxies** (1 proxy por cada nueva cuenta).
- Aumenta `fabrica.cuentas_por_dia` en `config.yaml` de forma progresiva (ej. de 5 a 15 al día) manteniendo las pausas aleatorias de 3 a 10 minutos entre creaciones.
