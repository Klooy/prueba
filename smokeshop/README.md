# Templo de Anubis — Referidos y menú del día

Sistema privado para una smoke shop:

- **Referidos**: registra clientes y cada uno recibe un código (`ANK-XXXXX`). Al registrar a alguien con el código de quien lo refirió, queda guardado quién trajo a quién.
- **Descuentos en pesos (COP)**: el administrador decide el valor de cada descuento. Lo asigna con el código del cliente en la página **Descuentos**, al registrar un referido (valor para quien refirió y bienvenida para el nuevo, ambos opcionales) o desde la ficha del cliente. Cada descuento se canjea una sola vez, uno por uno o con **Canjear todo**; el cliente ve su saldo en pesos en "Mi código".
- **Menú del día**: sube novedades (foto, precio, categoría, destacado) sin llevar inventario. Ocultar no borra; "Vaciar menú" deja todo listo para el día siguiente.
- **Pedidos por WhatsApp**: el cliente agrega productos del menú a su pedido y, al tocar **Pedir por WhatsApp**, se abre el chat de la tienda con el pedido ya escrito (productos, cantidades, total en pesos y, si quiere, su nombre, su código de cliente y una nota). El número se define en **Ajustes → Pedidos por WhatsApp**; mientras no haya uno, el menú se muestra sin carrito. El pedido se guarda en el navegador del cliente, no en la base de datos: cada pedido llega solo al chat.
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

Variables opcionales: `SECRET_KEY`, `DATABASE` (ruta del SQLite; por defecto `instance/smokeshop.db`), `UPLOAD_FOLDER`, `PORT` y `STORE_WHATSAPP` (número para pedidos, p. ej. `573001234567`; se usa mientras en **Ajustes** no haya uno guardado).

## Vista previa en Vercel (solo para ver el diseño)

1. En Vercel: **Add New → Project**, importa el repositorio y pon `smokeshop` como **Root Directory**.
2. En **Environment Variables** agrega `ADMIN_PASSWORD` (la contraseña del panel), `SECRET_KEY` (un texto largo y aleatorio) y `STORE_WHATSAPP` (el WhatsApp que recibe los pedidos, con indicativo: `573001234567`).
3. **Deploy**.

En Vercel la app arranca con productos y clientes de ejemplo (`DEMO_DATA=1` por defecto; pon `0` para desactivarlo). Todo lo que cambies se guarda en `/tmp` y **se pierde** cuando Vercel recicla la función, y cada instancia tiene su propia copia; por eso el WhatsApp conviene ponerlo en `STORE_WHATSAPP` y no solo en Ajustes. Para uso real hace falta Postgres + almacenamiento de archivos, o un hosting con disco persistente.

## Rutas

| Ruta | Quién | Para qué |
|---|---|---|
| `/` | Público | Menú del día |
| `/mi-codigo` | Clientes | Ver sus referidos y descuentos con su código |
| `/r/<código>` | Invitados | Página de invitación |
| `/admin` | Dueño | Panel: clientes, canjear, menú, ajustes |

El nombre de la tienda, la frase del menú, el prefijo de los códigos y el WhatsApp para pedidos se cambian en **Ajustes**.

## Pruebas

```bash
python -m pytest -q
```
