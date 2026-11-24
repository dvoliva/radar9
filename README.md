# RADAR 9

Un periódico de inteligencia profesional en español, con nueve campos, briefing según el
tiempo disponible y un archivo de ediciones que no cambia al día siguiente. Reúne fuentes
públicas primarias; no es un sitio de HP ni utiliza información interna.

**[Leer RADAR 9](https://dvoliva.github.io/radar9/)** · [Ejecuciones](https://github.com/dvoliva/radar9/actions/workflows/radar.yml) · [Manual de operación y recuperación](docs/operacion.md)

## Leer y explorar

- La portada prioriza acontecimientos por relevancia. Escoger 5–30 minutos o lectura completa cambia la **selección**, no recorta los análisis.
- Cada artículo tiene dirección estable, resumen ejecutivo, hechos y contexto con referencias, análisis, implicancias, señales de seguimiento y glosario.
- El archivo combina texto completo con filtros por campo, industria, empresa, fuente, edición y fechas de publicación. Una edición guarda su orden, briefing y etiquetas de novedad.
- El sitio distingue la publicación original, la detección y la incorporación. Una novedad de días anteriores no se fecha como si fuera de hoy.
- Favoritos, progreso, marcas de lectura, tema y tamaño de letra viven en el navegador. No hay cuenta, sincronización entre dispositivos, analítica de terceros ni llamadas a IA desde el cliente.

## Arquitectura

| Componente | Responsabilidad |
| --- | --- |
| [Catálogos](config/) | Nueve campos permanentes, fuentes permitidas y tarifas de referencia. |
| [Conectores](radar/feeds.py) | Descubrimiento oficial, control de acceso, extracción de texto, identidad y fecha. |
| [Clasificador](radar/classify.py) | Relevancia, campos, industrias y empresas mediante reglas, antes de gastar tokens. |
| [Editor modular](radar/editor.py) | Adaptador OpenAI, contrato `Provider`, esquemas, evidencias y presupuesto con reserva previa. |
| [Control editorial](radar/quality.py) | Idioma, estructura, longitud, citas, cronología e integridad referencial. |
| [Actualización](radar/refresh.py) y [persistencia](radar/store.py) | Detección, selección, observabilidad y archivo inmutable con escrituras atómicas. |
| [Archivo público](data/) | Artículos únicos y ediciones fechadas en Git. No se almacena el texto íntegro de las fuentes. |
| [Generador](radar/site.py) y [recursos web](web/) | HTML estático, CSS y JavaScript locales. No hay servidor de aplicación ni base de datos de pago. |
| [Workflow](.github/workflows/radar.yml) | Recolección a las **07:15 `America/Santiago`**, validación, build, prueba de navegador y publicación atómica en Pages. |

## Ejecución local

Requiere Python 3.12. En PowerShell, desde la raíz del repositorio:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m radar.quality
.\.venv\Scripts\python.exe -m radar.site --output dist
.\.venv\Scripts\python.exe -m radar.verify dist
.\.venv\Scripts\python.exe -m radar.preview
```

Abrir `http://127.0.0.1:8919/radar9/`. El servidor local solo expone el artefacto estático y solo en loopback. Para probar Chromium:

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.tools\browsers"
.\.venv\Scripts\python.exe -m playwright install chromium --only-shell
.\.venv\Scripts\python.exe -m checks.browser
```

En Linux/macOS, usar `.venv/bin/python` en vez de `.\.venv\Scripts\python.exe`. Para **solo construir y leer**, basta [requirements.txt](requirements.txt); no hacen falta Chromium, OpenAI ni credenciales de GitHub.

## Activación

1. En **Settings → Pages → Build and deployment → Source**, seleccionar **GitHub Actions**. Un push a `main` publica el histórico versionado sin esperar a las fuentes ni requerir la API.
2. Para nuevas ediciones automáticas, añadir `OPENAI_API_KEY` como **[secreto de Actions](https://github.com/dvoliva/radar9/settings/secrets/actions)**, no como variable ni en un fichero del repositorio. Elegir una clave de proyecto, con acceso a los modelos configurados.
3. Para los resultados trimestrales de HP Inc. y Dell depositados en SEC EDGAR, añadir también `RADAR9_SEC_CONTACT` como secreto: un correo real que se autorice a incluir en las solicitudes a la SEC. Sin este dato, esos dos conectores no hacen peticiones; el resto sigue funcionando.
4. Desde [Actions → RADAR 9](https://github.com/dvoliva/radar9/actions/workflows/radar.yml), seleccionar **Run workflow → main**. También se ejecuta diariamente según el huso de Santiago. Actions puede empezar tarde.
5. Verificar que las fases de actualización, construcción, despliegue y comprobación de la revisión pública terminan correctamente. **Un build local no equivale a una publicación.**

Sin clave se pueden consultar fuentes y actualizar su estado, pero no se fabrica contenido ni se reemplaza la última edición. Las notas iniciales de curaduría se identifican como tales. El directorio distingue las fuentes automáticas de las documentales.