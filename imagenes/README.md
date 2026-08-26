# Miniaturas

Las imágenes no se copian durante la extracción inicial. Los JSON conservan `miniatura_origen` y `portada_origen`.

Si posteriormente se cuenta con autorización para conservar copias, se podrán descargar, validar y convertir a WebP usando una estructura como:

```text
imagenes/<slug>/episodio-0001.webp
```

De esta manera una URL rota puede usar como respaldo la portada del anime o una imagen genérica de AnimeJD, sin inflar innecesariamente el historial de Git.

