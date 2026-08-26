# Catálogo de series finalizadas de JKAnime

Archivo independiente de metadatos públicos de series marcadas como finalizadas en el directorio de JKAnime.

Este repositorio no forma parte de AnimeJD y no modifica su código, su base de datos ni su despliegue. No contiene videos, credenciales ni copias de reproductores. Conserva metadatos, enlaces públicos a las páginas originales y referencias de miniaturas para revisión posterior.

## Organización

```text
catalogo/
  index.json
  manifest.json
  errores.json
  animes/
    nombre-del-anime.json
imagenes/
  README.md
```

`index.json` es un índice ligero. Cada archivo dentro de `catalogo/animes/` contiene la ficha y la lista pública de episodios de una serie. El extractor es reanudable y no vuelve a solicitar fichas que ya terminaron correctamente salvo que se use `--force`.

## Actualizar

```powershell
python extractor.py --todo
```

Opciones útiles:

```powershell
python extractor.py --directorio
python extractor.py --episodios --workers 3 --delay 0.5
python extractor.py --episodios --force
```

Los datos pertenecen a sus respectivos autores y proveedores. Antes de reutilizarlos o publicarlos, deben revisarse los permisos, las condiciones del sitio de origen y los derechos aplicables.

