# Templo de Anubis — Referidos y menú del día

Sistema privado para una smoke shop:

- **Referidos**: los clientes se registran solos en `/registro` (o desde el enlace de un amigo, `/r/<código>`), o los registras tú en el panel. Cada uno recibe un código (`ANK-XXXXX`) y un enlace para invitar. Cada persona que se registra con el enlace o el código de alguien le suma a quien la invitó el **valor por referido**, en pesos, que defines en **Ajustes → Programa de referidos**; ese valor queda como un descuento para canjear en la tienda. En "Mi código" cada cliente ve su enlace, cuántos referidos tiene, cuánto vale cada uno y su saldo. El registro pide confirmar la mayoría de edad y autorizar el uso de los datos. En el panel, los clientes que se registraron por la web salen marcados como "Web", y si borras un registro falso se le quita a quien lo invitó el descuento que aún no había usado.
- **Descuentos en pesos (COP)**: el administrador decide el valor de cada descuento. Lo asigna con el código del cliente en la página **Descuentos**, al registrar un referido (valor para quien refirió y bienvenida para el nuevo, ambos opcionales) o desde la ficha del cliente. Cada descuento se canjea una sola vez, uno por uno o con **Canjear todo**; el cliente ve su saldo en pesos en "Mi código".
- **Menú del día**: sube novedades (foto, precio, categoría, destacado) sin llevar inventario. Ocultar no borra; "Vaciar menú" deja todo listo para el día siguiente.
- **Medidas y precios**: un producto puede venderse en varias medidas, cada una con su precio (10 g → $50.000, 20 g → $90.000, o "Caja x 3", "1 onza"…). Se escriben en el formulario del producto; los precios aceptan `50000`, `50.000`, `50k` o `50 mil`. En el menú el cliente toca la medida, el precio cambia y la agrega al pedido; cada medida va como una línea aparte en el carrito, en el mensaje de WhatsApp y en la ficha del pedido. Con medidas, el precio general del producto queda en el más barato ("desde").
- **Pedidos por WhatsApp**: el cliente agrega productos del menú a su pedido y, para el domicilio, elige el **método de pago** (la lista se define en Ajustes), comparte su **ubicación GPS** o escribe su dirección (al menos una de las dos) y, si quiere, agrega una **foto de la calle o la fachada**. Al tocar **Pedir por WhatsApp** se abre el chat de la tienda con el pedido ya escrito: número de pedido, productos, total en pesos, pago, un enlace de Google Maps para el domiciliario, la dirección, el enlace de la foto y, si quiere, su nombre, su código de cliente y una nota. WhatsApp no deja que una página web envíe el pin nativo de ubicación; por eso va el enlace de Google Maps. El número de la tienda se define en **Ajustes → Pedidos por WhatsApp**; mientras no haya uno, el menú se muestra sin carrito. El carrito vive en el navegador del cliente hasta que lo envía.
- **Historial de pedidos**: cada pedido enviado queda en **Pedidos** con el mismo número que llega al chat (`#0001`). Los precios se toman del menú del día en el servidor, no del navegador. Si el cliente vuelve a enviar el mismo carrito, se actualiza ese pedido en vez de duplicarlo. Se filtra por estado (nuevo, confirmado, entregado, cancelado) y se busca por número, nombre o código. La ficha del pedido muestra los productos, los datos de entrega (pago, botón para abrir el mapa, dirección y foto), la nota y, si el cliente escribió su código, sus descuentos pendientes. La barra del panel cuenta los pedidos nuevos y la ficha de cada cliente lista sus pedidos. Las horas van en hora de Colombia (UTC−5).
- **Vista pública**: menú del día filtrable por categoría, consulta de "Mi código" y enlace de invitación para compartir por WhatsApp.
- **Datos**: en local, un archivo SQLite; en producción, Postgres en Supabase (`DATABASE_URL`) y las fotos en Supabase Storage.
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

Variables opcionales: `SECRET_KEY`, `DATABASE` (ruta del SQLite; por defecto `instance/smokeshop.db`), `DATABASE_URL` (Postgres; si está, se usa en vez de SQLite), `UPLOAD_FOLDER`, `PORT`, `STORE_WHATSAPP` (número para pedidos, p. ej. `573001234567`; se usa mientras en **Ajustes** no haya uno guardado) y las de Supabase Storage (`SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `SUPABASE_BUCKET`).

## Producción: Vercel + Supabase

1. **Supabase**: crea un proyecto (región *East US (North Virginia)*, la misma de Vercel) y, en **Storage**, un bucket público llamado `productos`.
2. **Vercel**: **Add New → Project**, importa el repositorio y pon `smokeshop` como **Root Directory**.
3. En **Environment Variables** de Vercel:

   | Variable | Valor |
   |---|---|
   | `DATABASE_URL` | Cadena de conexión de Supabase: **Connect → Transaction pooler** (puerto 6543), con tu contraseña |
   | `SUPABASE_URL` | `https://<proyecto>.supabase.co` |
   | `SUPABASE_SECRET_KEY` | Clave secreta (`sb_secret_…`) o `service_role` de **Project Settings → API Keys** |
   | `ADMIN_PASSWORD` | Contraseña del panel |
   | `SECRET_KEY` | Un texto largo y aleatorio |
   | `STORE_WHATSAPP` | WhatsApp que recibe los pedidos, con indicativo: `573001234567` |

4. **Deploy**.

Las tablas se crean solas en el primer arranque, con *Row Level Security* activado y sin políticas: nadie puede leerlas con la API pública de Supabase, solo la app con la contraseña de la base. Supabase lo muestra como un aviso informativo; es lo esperado. Con una base propia la app arranca vacía (sin datos de ejemplo). Las fotos grandes se reducen en el navegador antes de subirlas, porque Vercel no recibe envíos de más de 4,5 MB.

Sin `DATABASE_URL`, Vercel sirve como vista previa: arranca con productos y clientes de ejemplo (`DEMO_DATA=1`) y todo lo que cambies se guarda en `/tmp` y **se pierde** cuando la función se recicla.

## Rutas

| Ruta | Quién | Para qué |
|---|---|---|
| `/` | Público | Menú del día |
| `/registro` | Público | Registro de clientes; con el enlace `/r/<código>` queda guardado quién los invitó |
| `/mi-codigo` | Clientes | Ver su enlace para invitar, sus referidos y sus descuentos |
| `/r/<código>` | Invitados | Enlace de referido: invitación y registro |
| `/pedido` (POST) | Clientes | Guarda el pedido del carrito y abre el WhatsApp de la tienda |
| `/admin` | Dueño | Panel: pedidos, clientes, descuentos, menú, ajustes |

El nombre de la tienda, la frase del menú, el prefijo de los códigos, el WhatsApp para pedidos, los métodos de pago, el valor de cada referido y si los clientes pueden registrarse solos se cambian en **Ajustes** (el engranaje de arriba en el panel). Ajustes también avisa si el WhatsApp guardado salió de la variable `STORE_WHATSAPP` de Vercel.

## Pruebas

```bash
python -m pytest -q
TEST_DATABASE_URL=postgresql://postgres@localhost:5432/pruebas python -m pytest -q   # la misma suite sobre Postgres
```

Con `TEST_DATABASE_URL` cada prueba borra y recrea el esquema `public` de esa base: usa una base solo para pruebas.
