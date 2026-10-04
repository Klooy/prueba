# Templo de Anubis — Referidos y menú del día

Sistema privado para una smoke shop:

- **Referidos**: registra clientes y cada uno recibe un código (`ANK-XXXXX`). Si un cliente nuevo llega con el código de otro, quien lo refirió gana un descuento y el nuevo recibe uno de bienvenida. Cada *N* referidos hay un premio extra.
- **Descuentos**: lista de descuentos pendientes por cliente, botón **Canjear** (cada descuento se usa una sola vez), regalos manuales y una **caja rápida** para buscar al cliente por su código.
- **Menú del día**: sube novedades (foto, precio, categoría, destacado) sin llevar inventario. Ocultar no borra; "Vaciar menú" deja todo listo para el día siguiente.
- **Vista pública**: menú del día filtrable por categoría, consulta de "Mi código" y enlace de invitación para compartir por WhatsApp.
- **Diseño**: mobile-first, estética de templo nocturno de lujo (oro, jade y humo violeta). El logo es una ilustración de Anubis con un amuleto de cannabis; `python tools/brand_assets.py imagen.jpg` genera a partir de ella la imagen de la portada, el logo del encabezado y los iconos.

## Ejecutar

```bash
cd smokeshop
pip install -r requirements-dev.txt
python app.py              # http://localhost:5000
```

La primera vez que entres a `/admin` te pedirá crear la contraseña del panel.

En producción:

```bash
SECRET_KEY="una-clave-larga" gunicorn -w 2 -b 0.0.0.0:8000 app:app
```

Variables opcionales: `SECRET_KEY`, `DATABASE` (ruta del SQLite; por defecto `instance/smokeshop.db`), `UPLOAD_FOLDER`, `PORT`.

## Vista previa en Vercel (solo para ver el diseño)

1. En Vercel: **Add New → Project**, importa el repositorio y pon `smokeshop` como **Root Directory**.
2. En **Environment Variables** agrega `ADMIN_PASSWORD` (la contraseña del panel) y `SECRET_KEY` (un texto largo y aleatorio).
3. **Deploy**.

En Vercel la app arranca con productos y clientes de ejemplo (`DEMO_DATA=1` por defecto; pon `0` para desactivarlo). Todo lo que cambies se guarda en `/tmp` y **se pierde** cuando Vercel recicla la función, y cada instancia tiene su propia copia. Para uso real hace falta Postgres + almacenamiento de archivos, o un hosting con disco persistente.

## Rutas

| Ruta | Quién | Para qué |
|---|---|---|
| `/` | Público | Menú del día |
| `/mi-codigo` | Clientes | Ver sus referidos y descuentos con su código |
| `/r/<código>` | Invitados | Página de invitación |
| `/admin` | Dueño | Panel: clientes, canjear, menú, ajustes |

Los porcentajes, el prefijo de los códigos, el nombre de la tienda y su WhatsApp se cambian en **Ajustes**.

## Pruebas

```bash
python -m pytest -q
```
