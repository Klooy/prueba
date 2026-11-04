# Templo de Anubis — Referidos y menú del día

Sistema privado para una smoke shop:

- **Modo privado** (activado de entrada; **Ajustes → Privacidad**): solo tus clientes ven el menú y hacen pedidos. Tú decides quién es cliente: lo registras en el panel y le mandas su código con el botón de WhatsApp de su ficha. Quien abre la página sin código ve una puerta para escribirlo (nada de productos ni precios); el celular lo recuerda 60 días. El pedido llega con el nombre y el código de quien pide. Nadie se registra solo, los enlaces de invitación llevan a la puerta y los clientes no ven el botón de compartir (la tienda sí, desde el panel o el menú). Los referidos siguen sumando: el amigo le escribe a la tienda de parte de un cliente y tú lo registras con el código de ese cliente (botón **Registrar referido** en su ficha). Si borras a un cliente, pierde el acceso. Los enlaces de productos que compartes en tus estados conservan su vista previa con foto, pero llevan a la puerta.
- **Fuera de buscadores y con freno a intentos**: la página no aparece en Google ni en otros buscadores (`robots.txt`, `<meta name="robots">` y la cabecera `X-Robots-Tag`), aunque WhatsApp sí puede armar las vistas previas. El panel y la puerta limitan los intentos seguidos desde una misma conexión (contraseña: 10 cada 15 minutos; códigos de cliente: 20 cada 10 minutos) para que nadie los adivine a la fuerza.
- **Referidos**: los registras tú en el panel o, con el modo privado apagado, los clientes se registran solos en `/registro` (o desde el enlace de un amigo, `/r/<código>`). Cada uno recibe un código (`ANK-XXXXX`) y un enlace para invitar. Cada persona que se registra con el enlace o el código de alguien le suma a quien la invitó el **valor por referido**, en pesos, que defines en **Ajustes → Programa de referidos**; ese valor queda como un descuento para canjear en la tienda. En "Mi código" cada cliente ve su enlace, cuántos referidos tiene, cuánto vale cada uno y su saldo. El registro pide confirmar la mayoría de edad y autorizar el uso de los datos. En el panel, los clientes que se registraron por la web salen marcados como "Web", y si borras un registro falso se le quita a quien lo invitó el descuento que aún no había usado.
- **Descuentos en pesos (COP)**: el administrador decide el valor de cada descuento. Lo asigna con el código del cliente en la página **Descuentos**, al registrar un referido (valor para quien refirió y bienvenida para el nuevo, ambos opcionales) o desde la ficha del cliente. Cada descuento se canjea una sola vez, uno por uno o con **Canjear todo**; el cliente ve su saldo en pesos en "Mi código".
- **Menú del día**: sube novedades (foto, precio, categoría, destacado) sin llevar inventario. Ocultar no borra; "Vaciar menú" deja todo listo para el día siguiente.
- **Medidas y precios**: un producto puede venderse en varias medidas, cada una con su precio (10 g → $50.000, 20 g → $90.000, o "Caja x 3", "1 onza"…). Se escriben en el formulario del producto; los precios aceptan `50000`, `50.000`, `50k` o `50 mil`. En el menú el cliente toca la medida, el precio cambia y la agrega al pedido; cada medida va como una línea aparte en el carrito, en el mensaje de WhatsApp y en la ficha del pedido. Con medidas, el precio general del producto queda en el más barato ("desde").
- **Pedidos por WhatsApp**: el cliente agrega productos del menú a su pedido y, para el domicilio, elige el **método de pago** (la lista se define en Ajustes), comparte su **ubicación GPS** o escribe su dirección (al menos una de las dos) y, si quiere, agrega una **foto de la calle o la fachada**. Al tocar **Pedir por WhatsApp** se abre el chat de la tienda con el pedido ya escrito: número de pedido, productos, total en pesos, pago, un enlace de Google Maps para el domiciliario, la dirección, el enlace de la foto y, si quiere, su nombre, su código de cliente y una nota. WhatsApp no deja que una página web envíe el pin nativo de ubicación; por eso va el enlace de Google Maps. El número de la tienda se define en **Ajustes → Pedidos por WhatsApp**; mientras no haya uno, el menú se muestra sin carrito. El carrito vive en el navegador del cliente hasta que lo envía.
- **Compartir productos (estados de WhatsApp)**: cada producto del menú tiene un botón para compartir, sobre su foto en el menú y al lado de **Ocultar** en el panel. En el celular abre el menú de compartir del teléfono: en WhatsApp se elige **Mi estado** o un chat. Se comparte el enlace del producto (`/p/<id>`) con su nombre y su precio, y WhatsApp arma la vista previa **con la foto**, porque esa página trae las etiquetas Open Graph (`og:title`, `og:description`, `og:image`). La foto de la vista previa es una versión liviana que genera el servidor (JPEG de hasta 1080 px y menos de 250 KB; WhatsApp no muestra fotos más pesadas) y Vercel la guarda en caché. Quien abre el enlace llega al menú, justo en ese producto, y puede pedirlo. Las demás páginas usan la imagen de la tienda como vista previa. La vista previa solo funciona con la página publicada: las vistas previas de Vercel piden iniciar sesión y WhatsApp no puede leerlas.
- **Pedidos abiertos o en pausa**: en el Inicio del panel, **Pausar pedidos** detiene los pedidos nuevos cuando no das abasto (y **Reanudar pedidos** los vuelve a abrir). En **Ajustes** se pone el horario en que se reciben pedidos (puede pasar la medianoche, como de 6:00 p. m. a 2:00 a. m.) y un máximo de pedidos por día. Mientras están cerrados, el menú se ve con un aviso y el carrito no envía pedidos nuevos (el cliente sí puede reenviar uno que ya mandó y sigue abierto); el servidor también los rechaza. Los pedidos cancelados no cuentan para el máximo.
- **Agotado**: en **Menú** del panel, **Agotado** marca un producto que se acabó, y en los productos con medidas se marca cada medida por separado. Lo agotado sigue en el menú, tachado o con la etiqueta "Agotado", pero no se puede pedir: el carrito lo quita y el servidor no lo acepta. Se vuelve a tocar cuando vuelve.
- **Descuentos al pedir**: quien entró con su código ve en el carrito su saldo de descuentos y puede usarlo; no pasa del valor de los productos. El mensaje llega con el descuento restado. Al **confirmar** (o entregar) el pedido se descuenta solo de su saldo; si un descuento era mayor, lo que sobra le queda como "Saldo que quedó", y si el pedido se **cancela** se le devuelve. Si su saldo ya no alcanzaba, el panel avisa cuánto cobrarle de más.
- **Domicilio**: en **Ajustes → Domicilio** se pone un valor fijo o un valor por barrio (`Chapinero = 5000`, uno por línea; 0 = gratis en ese barrio), y si quieres, "gratis desde" un valor en productos. Con barrios, el cliente elige el suyo (o "Otro barrio": el valor general, o "por confirmar" si no hay). El carrito muestra productos, descuento, domicilio y total, y cuánto le falta para el domicilio gratis; el servidor lo recalcula y lo escribe en el mensaje.
- **Mis pedidos**: en "Mi código" el cliente ve sus últimos pedidos, si están recibidos, confirmados, entregados o cancelados, y con **Repetir** llena el carrito con lo mismo (lo que ya no hay o está agotado se quita solo) y lo abre.
- **Acceso de cada cliente**: en su ficha, **Pausar acceso** le cierra el menú privado y "Mi código" sin borrarlo (conservas su historial), y **Cambiar código** le da uno nuevo si regaló o publicó el suyo: el anterior deja de servir.
- **Resumen y respaldo**: desde el Inicio del panel, ventas (lo que pagan los pedidos confirmados o entregados), pedidos, ticket promedio y clientes nuevos de los últimos 7 días, 30 días, 3 meses o 1 año; ventas de las últimas 8 semanas; lo más pedido; los clientes que más piden; referidos y descuentos. Allí mismo se descargan **clientes, pedidos y descuentos** en CSV para abrir en Excel o Google Sheets (separado por punto y coma, con tildes; un texto que empieza con `=`, `+`, `-` o `@` va con un apóstrofo para que Excel no lo tome como fórmula).
- **Historial de pedidos**: cada pedido enviado queda en **Pedidos** con el mismo número que llega al chat (`#0001`). Los precios se toman del menú del día en el servidor, no del navegador. Si el cliente vuelve a enviar el mismo carrito, se actualiza ese pedido en vez de duplicarlo. Se filtra por estado (nuevo, confirmado, entregado, cancelado) y se busca por número, nombre o código. La ficha del pedido muestra los productos, los datos de entrega (pago, botón para abrir el mapa, dirección y foto), la nota y, si el cliente escribió su código, sus descuentos pendientes. La barra del panel cuenta los pedidos nuevos y la ficha de cada cliente lista sus pedidos. Las horas van en hora de Colombia (UTC−5).
- **Vista de los clientes**: menú del día filtrable por categoría y "Mi código" (referidos y descuentos; con el modo privado apagado, también el enlace de invitación para compartir por WhatsApp).
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

Variables opcionales: `SECRET_KEY`, `DATABASE` (ruta del SQLite; por defecto `instance/smokeshop.db`), `DATABASE_URL` (Postgres; si está, se usa en vez de SQLite), `UPLOAD_FOLDER`, `PORT` y las de Supabase Storage (`SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `SUPABASE_BUCKET`).

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

   Marca cada variable para *Production* y *Preview*: así las vistas previas usan la misma base de datos que la página publicada.
4. **Deploy**.
5. En el panel de la página publicada, **Ajustes → Pedidos por WhatsApp**: el número que recibe los pedidos. Solo se configura ahí (la variable `STORE_WHATSAPP` de versiones anteriores ya no se usa y se puede borrar).

Las tablas se crean solas en el primer arranque, con *Row Level Security* activado y sin políticas: nadie puede leerlas con la API pública de Supabase, solo la app con la contraseña de la base. Supabase lo muestra como un aviso informativo; es lo esperado. Con una base propia la app arranca vacía (sin datos de ejemplo). Las fotos grandes se reducen en el navegador antes de subirlas, porque Vercel no recibe envíos de más de 4,5 MB.

Sin `DATABASE_URL`, Vercel sirve como vista previa: arranca con productos y clientes de ejemplo (`DEMO_DATA=1`) y todo lo que cambies se guarda en `/tmp` y **se pierde** cuando la función se recicla. El panel lo avisa arriba en todas sus páginas, con el enlace al panel de la página publicada, para no guardar ahí ajustes como el WhatsApp. En una vista previa de Vercel con base de datos, el panel también dice que es una vista previa.

## Rutas

| Ruta | Quién | Para qué |
|---|---|---|
| `/` | Público | Menú del día |
| `/registro` | Público | Registro de clientes; con el enlace `/r/<código>` queda guardado quién los invitó (cerrado con el modo privado) |
| `/mi-codigo` | Clientes | Ver su enlace para invitar, sus referidos y sus descuentos |
| `/r/<código>` | Invitados | Enlace de referido: invitación y registro (con el modo privado, lleva a la puerta) |
| `/robots.txt` | Buscadores | Les pide no mostrar la página; deja leer las vistas previas de WhatsApp |
| `/pedido` (POST) | Clientes | Guarda el pedido del carrito y abre el WhatsApp de la tienda |
| `/p/<id>` | Público | Enlace para compartir un producto: vista previa con su foto y el menú abierto en él |
| `/p/<id>/<foto>.jpg` | WhatsApp y redes | Foto liviana del producto para la vista previa |
| `/admin` | Dueño | Panel: pedidos, clientes, descuentos, menú, ajustes |
| `/admin/resumen` | Dueño | Ventas, pedidos, productos, clientes, referidos y descuentos de un período |
| `/admin/respaldo/<clientes\|pedidos\|descuentos>.csv` | Dueño | Copia de seguridad para Excel |

El nombre de la tienda, la frase del menú, el prefijo de los códigos, el WhatsApp para pedidos, los métodos de pago, el horario y el máximo de pedidos por día, el domicilio, el modo privado, el valor de cada referido y si los clientes pueden registrarse solos (solo con el modo privado apagado) se cambian en **Ajustes** (el engranaje de arriba en el panel). Junto al WhatsApp, Ajustes dice dónde quedó guardado; el carrito del menú también muestra a qué número llega el pedido.

## Pruebas

```bash
python -m pytest -q
TEST_DATABASE_URL=postgresql://postgres@localhost:5432/pruebas python -m pytest -q   # la misma suite sobre Postgres
```

Con `TEST_DATABASE_URL` cada prueba borra y recrea el esquema `public` de esa base: usa una base solo para pruebas.
