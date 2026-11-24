# Operar RADAR 9

## Lectura de estados

La **fecha de edición** pertenece al día de Santiago, no al reloj del lector. Su sello identifica cuándo se cerró la composición. La **última consulta exitosa** indica observación de fuentes; no demuestra que todas hayan respondido. En el directorio se informa por fuente. La portada pública solo cambia al publicar un artefacto completo. El paso final de Actions compara su `build-id.json` con el commit construido y comprueba la portada, no solo un código 200 de una versión previa.

El horario está definido con `timezone: America/Santiago` y `cron: '15 7 * * *'` ([documentación oficial](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)). No usar dos crons UTC ni compensar manualmente el cambio de hora. El horario no garantiza puntualidad: GitHub puede retrasar o desactivar calendarios de repositorios públicos inactivos. Revisar la pestaña Actions si deja de ejecutarse. La concurrencia abarca hasta el fin del despliegue.

La publicación normal de código en `main` solo construye y despliega. Para comprobar de extremo a extremo una entrega desde Git sin acceso a la consola de Actions, un mantenedor puede añadir `[actualizar]` al mensaje del commit enviado a `main`: se usa exactamente el mismo job y los mismos límites que en la ejecución manual o programada. No se aplica a pull requests.

## Qué se edita y qué se conserva

- **Registro de fuentes:** [`config/sources.json`](../config/sources.json). Usar subdominios exactos, HTTPS y endpoints primarios. Antes de habilitar un conector revisar autoría, condiciones, robots, tipo, disponibilidad, fecha y URL canónica de un documento real. No añadir proxies, mirrors, cookies de acceso ni fuentes de prensa. Si una fuente prohíbe el acceso, desactivarla o usar otro endpoint *oficial y permitido*. Una fuente documental no se anuncia como conector operativo.
- **Resultados oficiales de HP Inc. y Dell:** los conectores de la SEC requieren el secreto `RADAR9_SEC_CONTACT`: una dirección de contacto real facilitada por el operador de acuerdo con la política de acceso de EDGAR. Se envía a la SEC en el `User-Agent`, nunca al frontend, a OpenAI ni al log. No se infiere del perfil de Git. Si falta, los dos conectores se declaran no disponibles. Las certificaciones de ENERGY STAR son fichas regulatorias, **no** ventas, lanzamientos ni cuota de mercado.
- **Artículo:** `data/articles/<id>.json`. La identidad deriva de la URL canónica primaria. La referencia conserva título original, institución a través de su ID, día de origen, URL y fecha de verificación. El texto original completo y los fragmentos de verificación del modelo no se publican. Las cifras de empresas son afirmaciones atribuidas, no auditorías de RADAR 9.
- **Edición:** `data/editions/AAAA-MM-DD.json`. El briefing y la secuencia de IDs y etiquetas se congelan. Una ejecución tardía asigna el día local real; un reintento del mismo día no reordena lo publicado. La ventana de descubrimiento cubre hoy y los seis días anteriores. Un seguimiento editorial puede referenciar un artículo histórico ya publicado sin duplicarlo (`python -m radar.refresh --follow ID`, antes de cerrar la edición; requiere proveedor habilitado).
- **Operación:** `data/status.json` registra disponibilidad, últimas consultas y contadores; `data/detections.json`, la primera observación y huellas para evitar repetir rechazos sin cambios; `data/usage/AAAA-MM-DD.json`, reservas y consumo estimado. No hay credenciales ni copias de documentos en estos archivos. Git conserva estados anteriores.
- **Sin novedades:** con las fuentes consultadas satisfactoriamente y sin material admisible se puede cerrar una edición vacía explícita. Un bloqueo general, fallo del proveedor, falta de clave o presupuesto no equivale a «no hubo noticias». Se mantiene la edición válida. Si hay artículos válidos pero la cobertura es parcial, se indica en el briefing.

## IA y gasto

En GitHub, las variables se configuran en **Settings → Secrets and variables → Actions → Variables**; la clave va en **Secrets**. Localmente se leen variables de entorno del proceso: [`.env.example`](../.env.example) es una plantilla de nombres, **no se carga automáticamente**. No escribir una clave en comandos que queden en el historial ni enviarla por chat.

| Variable | Valor inicial en Actions | Uso |
| --- | --- | --- |
| `RADAR_MODEL_STANDARD` | `gpt-5.4-mini` | Breves y estándar. |
| `RADAR_MODEL_DEEP` | `gpt-5.4` | Análisis profundo sustentado por un documento extenso y de alta relevancia. |
| `RADAR_MAX_ARTICLES` | `4` | Máximo de intentos facturables por día, incluidos los fallidos; `0` desactiva gasto. |
| `RADAR_DAILY_USD` | `0.75` | Tope estimado diario en USD; `0` impide reservar. |
| `RADAR_{STANDARD,DEEP}_{INPUT,OUTPUT}_USD_M` | Catálogo de precios | Sobrescribir tarifas, en USD por millón de tokens. Un modelo desconocido sin tarifas detiene el procesamiento. |

Los modelos admiten JSON estructurado según sus fichas oficiales ([mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini), [profundo](https://developers.openai.com/api/docs/models/gpt-5.4)). La disponibilidad depende de la **cuenta**: si hay clave, cada actualización comprueba ambos perfiles con `GET /v1/models/{id}`, sin tokens de generación, incluso si la edición ya está cerrada. `data/status.json` distingue la comprobación de modelos de `last_ai_success` (generación aceptada por los controles editoriales). No se elige un sustituto más caro en silencio.

Ejemplo a tarifas estándar, sin contar descuentos de caché: 4.000 tokens de entrada y 1.500 de salida cuestan **USD 0,00975** en mini o **USD 0,0325** en el modelo profundo. Son ejemplos, no una factura ni un compromiso de precios. Cada reserva usa una cota conservadora de entrada y el máximo de salida; puede bloquear una generación aunque el coste final hubiera sido menor. El tope predeterminado equivale a **USD 22,50 en 30 días** al agotarlo diariamente. El coste real depende de la tarifa de la cuenta, impuestos y uso; revisar [precios](https://developers.openai.com/api/docs/pricing) y añadir alertas en el proyecto OpenAI. No se contrata infraestructura adicional. Pages y Actions están sujetos a las cuotas/condiciones de la cuenta.

En Actions la reserva se confirma en Git **antes** de cada POST de pago. Al recibir uso verificable se reemplaza la reserva por el coste estimado. Un corte o timeout deja retenida la reserva máxima. No hay reintento automático de una petición facturable ambigua. Nunca borrar reservas para forzar otro gasto. Las llamadas al proveedor no tienen herramientas, secretos en el prompt, URLs sugeridas por el modelo ni acceso al repositorio. Los documentos se tratan como datos externos. El contrato `Provider` en [el editor](../radar/editor.py) permite incorporar otro proveedor (por ejemplo DeepSeek) con validación equivalente, sin cambiar el sitio. No hay integración DeepSeek activa.

**Control editorial:** esquema JSON estricto y validación independiente en el proceso; evidencia literal que debe estar en el documento, cifras corroboradas en esos fragmentos, rechazo de copias largas, comprobaciones de español, campos y cronología. Son filtros conservadores, no una garantía semántica ni revisión humana. Las referencias para publicar se ensamblan en el proceso, no se confía al modelo la elección de una URL. Rechazar un texto es preferible a rellenar una categoría.

## Diagnóstico y recuperación

| Síntoma | Acción |
| --- | --- |
| Pages da 404 o el job `desplegar` está rojo | Comprobar [Pages](https://github.com/dvoliva/radar9/settings/pages) → **GitHub Actions**, que el entorno `github-pages` admite `main`, y reintentar el job fallido. La ruta es `/radar9/`, no la raíz del dominio. |
| Push rechazado por reglas de rama | Permitir el flujo autorizado de escritura de **datos** del job `actualizar` según las reglas del repositorio. Nunca desactivar controles generales ni forzar un push. Incorporar primero los cambios concurrentes y reintentar desde la punta actual de `main`. |
| `401`, modelo no accesible o enriquecimiento pendiente | Revisar secreto, proyecto, facturación y permisos del modelo sin imprimir claves. Si la última edición ya está cerrada, se conserva: las próximas incorporaciones entran en otra edición. |
| Sin noticias, fuente parcial o bloqueada | Revisar `fuente_no_disponible` / `fuente_parcial` y directorio. Un error de una fuente no valida sus noticias. No cambiar el user-agent para eludir restricciones. |
| Presupuesto agotado | Revisar registros diarios, tokens y tarifas. Esperar al siguiente día o decidir explícitamente otro tope. |
| Build o validación fallidos | El despliegue no se inicia. Ejecutar tests, `python -m radar.quality`, `python -m radar.site --output dist` y `python -m radar.verify dist` antes de reintentar. No «reparar» borrando el archivo histórico. |
| Interrupción local | Confirmar que no sigue ejecutándose ese proceso. Solo entonces retirar el bloqueo específico `.radar/refresh.lock`. Las escrituras son atómicas. Preservar reservas y reintentar; se recuperan artículos válidos aún no incluidos en una edición. |
| Errata histórica | Publicar una nota posterior de corrección identificada y enlazada. No sobrescribir una nota ya seleccionada. Las comprobaciones de Git rechazan borrar o modificar el contenido histórico en una publicación normal. |

Recuperación completa: clonar el repositorio en otro directorio, instalar las dependencias y reconstruir. Comparar el archivo con un commit conocido usando `git diff`. Si un cambio de **código** impide la publicación, revertirlo mediante un nuevo commit, conservando `data/articles` y `data/editions`; verificar y volver a desplegar. No se necesita recuperar datos de un navegador. Los favoritos locales, a diferencia del archivo periodístico, no tienen copia remota.

Las dependencias están fijadas y Dependabot propone actualizaciones semanales. Los pull requests se prueban sin secretos y no publican Pages. En producción solo el job de actualización recibe escritura en el repo; el build trabaja en lectura y el deploy solo recibe permisos Pages/OIDC. Nunca subir `.env`, datos internos, respuestas brutas del proveedor ni el entorno virtual.
