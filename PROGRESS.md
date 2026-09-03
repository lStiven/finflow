# Progreso

Dónde está el trabajo hoy: qué está hecho, qué sigue, qué está trabado.
Se lee al empezar una sesión y se actualiza al terminar un trabajo.

No se narra aquí lo que cambió (eso ya lo guarda git) ni *por qué* algo es como
es (eso vive en [docs/decisions.md](docs/decisions.md), que **no** se lee al
arrancar: se busca dentro cuando hay una duda concreta).

Última verificación contra el código y contra AWS: **2026-09-03**.

## Qué hace hoy la aplicación

Todo el backend de la versión 1 está terminado y probado:

- **Los movimientos entran solos.** Cada usuario reenvía los correos de su banco
  a su propia dirección en el buzón que la aplicación posee; un proceso lo
  revisa cada minuto, acepta solo los remitentes que ese usuario aprobó, y saca
  el movimiento del correo. Dos bancos tienen plantilla propia (Bancolombia y
  Lulo); el resto lo interpreta el modelo de lenguaje.
- **Las cuentas las declara el dueño**, nunca se descubren solas. Sin ninguna
  cuenta declarada la aplicación ya sirve: registra todo y lo deja sin asignar.
  Al declarar una cuenta, sus movimientos anteriores se le asocian.
- **Se puede escribir a mano y corregir.** Un movimiento que nunca llegó por
  correo se agrega, y cualquiera se edita o se borra — al borrarlo, la cuenta
  recupera la plata.
- **Los traslados no cuentan como gasto ni como ingreso.** Pagar la tarjeta
  desde una cuenta del mismo banco mueve los dos saldos; pagada desde otro
  banco, se registra a mano el lado que sí se conoce.
- **Los créditos se cobran solos lo que el mes les cobra.** Con la tasa, el día
  de corte, la cuota y los seguros declarados, cada corte cerrado deja escritos
  los intereses y cada seguro como movimientos con nombre. Pagar 2.000.000 sobre
  60.000.000 deja 58.920.622,87, no 58.000.000. Un préstamo o una hipoteca se
  **vigilan pero no se suman**: quedan fuera del patrimonio y de todos los
  totales. Una tarjeta sí cuenta: lo que debe *es* el gasto del mes.
- **Las inversiones sí suman al patrimonio**, a diferencia de los créditos: un
  CDT acumula su rendimiento y retiene; un fondo sin tasa se valora a mano y la
  diferencia queda como movimiento, para que la ganancia se vea como ingreso en
  lugar de aparecer de la nada en el saldo.
- **Entrar y recuperar la cuenta**: registrarse exige comprobar el correo con un
  código de seis dígitos; la contraseña olvidada se recupera con un enlace que
  dura 30 minutos; cambiarla cierra todas las sesiones abiertas con la anterior.
- **Reportes**: cuánto sumó un periodo, contra el periodo anterior, y la gráfica
  por categorías.

Los comercios se normalizan aparte: el texto del banco se convierte en un
comercio con nombre y categoría, y hay una pantalla para revisar y corregir.

**Estado técnico:** 50 operaciones de API en los cuatro contextos, cinco
procesos en la nube, 1394 pruebas de Python y 273 del frontend, todas en verde.
El contrato de la API y los tipos del frontend están sincronizados. Árbol de
trabajo limpio.

**Pantallas:** están todas menos una. Resumen, Transacciones (incluido crear,
trasladar y borrar), Cuentas (con la pantalla de financiación y su tabla de
amortización), Comercios, Reportes, Perfil, la guía para conectar el banco y las
tres guías. Falta **Configuración**, la única entrada del menú sin pantalla.
Las dieciocho se revisaron una por una en un navegador el 2026-09-03, y las
cifras se comprobaron contra la API.

**En cualquier pantalla, del teléfono más pequeño al monitor.** Comprobado a
320, 360, 390, 430, 768, 1024 y 1440 px, y con el teléfono acostado: ninguna
pantalla se va de lado, y a todas las secciones se llega sin escribir la
dirección. Abajo de 1024 px la barra inferior lleva Resumen, Transacciones,
Cuentas y **Más**, que abre el resto —Reportes, Comercios, Guías, la cuenta y
cerrar sesión—; de 1024 para arriba, la columna de la izquierda de siempre.

## Lo publicado va atrasado respecto al repositorio

Esto es lo más importante hoy. Todo lo de arriba funciona en el computador, pero
**lo que está en internet es más viejo**:

| | Repositorio | Publicado |
|---|---|---|
| API producción | 50 operaciones | 43 — le faltan préstamos, inversiones y borrar movimiento |
| API desarrollo | 50 operaciones | 43 — igual que producción |
| Web (ambas) | pestaña Traslado, borrar, financiación | ninguna de las tres |

Las dos APIs se actualizaron por última vez el 2026-09-02 y sí tienen la
verificación de correo y la recuperación de contraseña. Las dos webs
(`finflow-apk.pages.dev` y `finflow-dev-2tc.pages.dev`) responden, pero su
paquete es anterior al 2026-09-02: el trabajo de traslados, borrado y créditos
nunca se publicó en ningún lado.

| | Nombre | Buzón | Revisa cada |
|---|---|---|---|
| producción | `finflow` | `finflowingest@gmail.com` | 1 minuto |
| desarrollo | `finflow-dev` | `finflowdevelopment@gmail.com` | 5 minutos |

Producción ya tiene un usuario real. Los dos entornos existen completos —
tablas, secretos, respaldo puntual, alarmas — pero viven en la misma cuenta de
AWS (ver Trabas).

## Lo siguiente, en orden

1. **Publicar lo que ya está hecho.** Es lo único que separa el trabajo de estar
   en manos de quien lo usa. En desarrollo primero: `just deploy-dev` y, **en la
   misma sentada**, `just web-publish-dev` — si la web queda vieja frente a una
   API nueva, la pantalla se rompe (ya pasó el 2026-09-01). Cuando desarrollo
   corra unos días sin sorpresas, lo mismo en producción con `just deploy-prod`,
   `just web-publish` y `just smoke-prod`.

2. **Nadie recibe los avisos de producción.** Las tres alarmas de mensajes
   fallidos publican en un tema de notificaciones que hoy **no tiene ni un
   suscriptor**: si algo se cae, no llega correo a nadie. Desarrollo sí está
   suscrito y confirmado. Ojo: CloudFormation cree que la suscripción existe, así
   que volver a desplegar no la recrea — hay que suscribir el correo a mano y
   confirmarlo desde el buzón. (La alarma de facturación de 5 USD sí funciona y
   está en OK.)

3. **Ponerle tope al gasto del modelo de lenguaje.** Cada correo que ninguna
   plantilla reconoce llama a Gemini, y no hay ningún límite. Es lo único de esta
   lista que cuesta plata mientras falta.

4. **Nada agenda el cobro mensual de los créditos.** Hoy los intereses se
   registran cuando alguien abre la pantalla del crédito y pulsa *Actualizar*.
   Mientras tanto el saldo se queda atrás, y la propia pantalla lo dice ("Hay 4
   cortes sin registrar"). Ya existe una operación que barre todas las cuentas de
   un usuario de una vez; falta el disparador diario en la nube, que necesita algo
   que hoy no existe: una forma de recorrer todos los usuarios.

5. **La pantalla de Configuración**, la última que falta.

6. **Publicar automáticamente.** Hoy todo se construye y se despliega a mano
   desde el contenedor. Nada está sin probar, pero un arreglo puede quedarse
   olvidado en el computador mientras producción sigue vieja — que es exactamente
   lo que está pasando ahora mismo (punto 1).

## Huecos conocidos, sin urgencia

- **Una tarjeta no se puede desvincular de una cuenta.** Solo se puede agregar,
  así que una tarjeta puesta en la cuenta equivocada no se puede mover desde
  ningún lado. Tampoco se puede reabrir una cuenta cerrada. Borrar una cuenta no
  se va a hacer: una cuenta cerrada sigue explicando sus movimientos.
- **Un movimiento escrito a mano nunca crea un comercio.** Encuentra el comercio
  si ese nombre ya llegó alguna vez por correo; si no, se queda sin comercio para
  siempre.
- **Falta decidir si los comercios son de cada usuario o compartidos.** Hoy son
  de cada uno. Compartirlos coincidiría con la intuición, pero los nombres y las
  categorías son decisiones personales, y las veces que alguien visitó un negocio
  se filtrarían a otro.
- **A un préstamo se le puede poner cupo, y no se ve en ninguna parte.** El
  formulario lo ofrece porque el backend lo acepta para cualquier deuda, pero la
  barra de «disponible» solo se dibuja en una tarjeta. Es un dato que se pide y
  no se usa.
- **La lista de notificaciones lee todo antes de recortar.** Paginar achica la
  respuesta, no la lectura.
- **Lulo tiene plantilla a medias**: se construyó con cuatro alertas, así que una
  compra con tarjeta, un retiro o una comisión en Lulo todavía van al modelo.
- **Falta comprobar el filtro de autorizaciones con correos reales.** Una
  autorización y su cobro son dos correos distintos, y la regla que descarta la
  primera se escribió sin tener uno a la mano.
- **El proceso que lee la cola está copiado tres veces** (ingesta, comercios,
  finanzas). Es transporte, no reglas de negocio: debería ser uno solo. La copia
  ya causó una diferencia de comportamiento entre las tres.
- **Falta decidir qué hacer con el saldo que reporta el banco** en algunas
  alertas: o corrige el saldo que la aplicación lleva, o se guarda solo como
  referencia. Los correos llegan desordenados, así que corregir exige distinguir
  una alerta vieja de una actual.

## Trabas

- **Desarrollo y producción son la misma cuenta de AWS** (792884702854). Son dos
  usuarios distintos, lo que separa permisos pero no datos: solo el prefijo del
  nombre mantiene las dos cosas aparte. Dejará de ser una nota el día que
  producción guarde las finanzas de otras personas: un perfil mal escrito apunta
  un comando de ensayo a plata real. Los comentarios que dicen que están
  separadas exageran.
- **Los permisos del usuario de producción no se conocen del todo.** Lee
  secretos, pilas y tablas, pero hoy se confirmó que no puede consultar los
  detalles de una suscripción de avisos. Se descubre el resto al desplegar, no
  adivinando.

## Últimos trabajos terminados

- 2026-09-03 — **La app se adapta a cualquier teléfono.** El Resumen se iba de
  lado en todos ellos (una tarjeta pedía 440 px), Reportes y Comercios no se
  alcanzaban por debajo de 1024 px, y el eje de las gráficas se leía «1 5 6 1 1»
  porque los días se recortaban a un carácter. Barra inferior de tres secciones
  más **Más**, y una prueba que impide que una sección vuelva a quedar sin
  puerta. Verificado en siete anchos. Y una pasada estética encima: las listas
  ya no parten el renglón —una línea que se corta al final en vez de crecer
  hacia abajo—, la fecha de un movimiento no repite el año que ya dice el
  encabezado del mes, y una cuenta muestra su nombre completo en dos líneas en
  lugar de «Ahorros Bancolo…».
- 2026-09-03 — Revisión de las dieciocho pantallas en un navegador, de teléfono
  y de computador, con las cifras contrastadas contra la API. Cuatro arreglos,
  tres de ellos por decir algo que no era cierto: los meses ya no se escriben
  «Agosto De 2026»; un préstamo ya no dice «Debes» en el Resumen ni «resta de tu
  patrimonio» al crearlo, cuando ningún total lo cuenta; y las guías ya no
  meten préstamos, hipotecas, tarjetas e inversiones en la misma bolsa.
- 2026-09-03 — Los créditos e inversiones cobran su propio mes: intereses,
  seguros y retención quedan como movimientos legibles. Los de un préstamo o una
  hipoteca no entran en ningún total; los de una inversión sí, como ingreso.
  Siete operaciones nuevas, su pantalla y su guía.
- 2026-09-03 — Borrar un movimiento devuelve la plata a la cuenta, con una
  advertencia que dice qué va a pasar según el tipo de movimiento.
- 2026-09-02 — Aprobar Bancolombia ahora aprueba también su tercer dominio, así
  que un traslado entre cuentas propias deja de perderse en el filtro.
