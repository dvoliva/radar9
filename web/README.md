# Publicación web de RADAR 9

Desde la raíz del repositorio, con Python 3.12:

```console
python -m radar.site --output dist
python -B -m unittest discover -s tests -p test_site.py
```

`dist` es también la salida predeterminada. Debe estar vacío, no existir o pertenecer a una compilación anterior de RADAR 9. Publica **su contenido** bajo `/radar9/`, no el repositorio. El generador no hace peticiones de red. Una entrada inválida no sustituye la publicación anterior.

| Ruta después de `/radar9/` | Contenido |
| --- | --- |
| *(raíz)* | Portada, estado, briefing, plan de lectura y nueve campos |
| `hoy/` | Última edición disponible, aunque su fecha sea anterior a hoy |
| `archivo/` | Hemeroteca mensual, con todas las ediciones |
| `ediciones/AAAA-MM-DD/` | Edición permanente con su orden original |
| `articulos/<id>/` | Lector permanente (no cambia al corregir un titular) |
| `categorias/`, `categorias/<1–9>/` | Atlas y archivo por campo principal o secundario |
| `explorar/` | Archivo de artículos, búsqueda, filtros combinables y orden |
| `favoritos/`, `configuracion/` | Biblioteca y preferencias privadas del navegador |
| `fuentes/` | Organizaciones, modalidad documental o automática, tipo, enlace de origen y última comprobación |
| `assets/` | Estilos, scripts, dos gráficos vectoriales e índice público de búsqueda |

Cada ruta de contenido es una carpeta con `index.html`. Sirva los archivos por HTTP(S) con ese prefijo, no mediante `file://`. Las URL de artículo usan el ID si es un segmento seguro; en caso contrario, `~` y el SHA-256 del ID original. No se copian entradas JSON, credenciales, errores ni archivos arbitrarios. El índice local contiene únicamente texto y facetas editoriales.

El directorio enlaza al canal o a la página de origen solo cuando usa HTTPS, pertenece a los dominios del catálogo y no lleva credenciales ni consultas desconocidas. Conserva los filtros públicos de los registros EPA. En fuentes `documental`, `enabled: false` indica que no hay recolección automática: el estado y las fechas reflejan la verificación manual registrada, no una deshabilitación.

La zona de lectura de fechas y horas es **America/Santiago**. Las fechas de publicación sin hora se conservan como fechas civiles. El respaldo `santiago.tzif.b64` es la zona de dominio público de IANA **2026f**, distribuida en `tzdata` 2026.6 (TZif decodificado: SHA-256 `fd006953c2b442a2e1e66db2a967dd932a4824390f01cddd9c801ce63450c715`). Se usa solo si el sistema no ofrece esa zona; no es necesario instalar el paquete.

## Controles para navegación por teclado y pruebas

- En Inicio, Hoy o una edición: grupo «¿Cuánto tiempo tienes?», radios «5 min», «10 min», «15 min», «20 min», «30 min», «Completa». El plan estima 210 palabras/minuto. Selecciona lecturas que caben en el presupuesto, priorizando relevancia, después duración y después orden editorial. Si ninguna cabe, propone leer solo el resumen de la más relevante.
- En Explorar: `getByRole('searchbox', {name: 'Buscar en el archivo'})`. Abrir «Filtros combinables» para los selectores «Campo» y «Edición» (`name="campo"` y `name="edicion"`). Los valores son el número de campo y la fecha ISO. También hay título, cuerpo, empresa, industria, fuente, fechas y orden; las combinaciones se reflejan en la URL.
- En un artículo: botones «Guardar en favoritos» / «Quitar de favoritos» y «Marcar como leído» / «Marcar como no leído», con `aria-pressed`. «Compartir enlace» comparte o copia la URL permanente.
- En el lector y en Configuración: `getByLabel('Apariencia')` (`sistema`, `claro`, `oscuro`) y `getByLabel('Tamaño del texto')` (`normal`, `grande`, `mayor`).
- Sin JavaScript permanecen la portada, los artículos completos, el índice temático, las referencias y todas las ediciones. El plan interactivo, la búsqueda por facetas y los datos personales requieren JavaScript; no se envían a un servidor.
