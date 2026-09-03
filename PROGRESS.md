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
  Al declarar una cuenta, sus movimientos anteriores se le asocian. Una tarjeta
  puesta donde no era se desenlaza y se enlaza en la correcta, y sus movimientos
  se van con ella; una cuenta cerrada se puede volver a abrir.
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

**Estado técnico:** 52 operaciones de API en los cuatro contextos, cinco
procesos en la nube, 1443 pruebas de Python y 281 del frontend, todas en verde.
El contrato de la API y los tipos del frontend están sincronizados. Hay trabajo
sin confirmar en el árbol (desenlazar tarjeta, reabrir cuenta, el lector de
cola compartido).

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
| API producción | 52 operaciones | 43 — le faltan préstamos, inversiones, borrar movimiento, desenlazar tarjeta y reabrir cuenta |
| API desarrollo | 52 operaciones | 43 — igual que producción |
| Web (ambas) | pestaña Traslado, borrar, financiación, desenlazar, reabrir | ninguna |

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

2. **Ponerle tope al gasto del modelo de lenguaje.** Cada correo que ninguna
   plantilla reconoce llama a Gemini, y no hay ningún límite. Es lo único de esta
   lista que cuesta plata mientras falta.

3. **Nada agenda el cobro mensual de los créditos.** Hoy los intereses se
   registran cuando alguien abre la pantalla del crédito y pulsa *Actualizar*.
   Mientras tanto el saldo se queda atrás, y la propia pantalla lo dice ("Hay 4
   cortes sin registrar"). Ya existe una operación que barre todas las cuentas de
   un usuario de una vez; falta el disparador diario en la nube, que necesita algo
   que hoy no existe: una forma de recorrer todos los usuarios.

4. **La pantalla de Configuración**, la última que falta.

5. **Publicar automáticamente.** Hoy todo se construye y se despliega a mano
   desde el contenedor. Nada está sin probar, pero un arreglo puede quedarse
   olvidado en el computador mientras producción sigue vieja — que es exactamente
   lo que está pasando ahora mismo (punto 1).

## Huecos conocidos, sin urgencia

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
- **Una inversión ya vencida sigue acumulando en la pantalla.** El cálculo de
  «intereses pendientes» ignora la fecha de vencimiento, así que un CDT que
  venció hace un año muestra un rendimiento que crece cada mes. El registro de
  los cortes sí respeta el vencimiento; es solo lo proyectado lo que miente.
- **Valorar un fondo dos veces el mismo día con la misma cifra no hace nada.**
  Ir de 11 a 15, volver a 11 y subir otra vez a 15 en un mismo día deja el saldo
  en 11: el segundo movimiento tiene la misma identidad que el primero y la
  escritura condicional lo rechaza, mientras la respuesta dice 200.
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
  secretos, pilas y tablas, suscribe un correo al tema de avisos, lista los
  suscriptores y las alarmas; lo que hoy se comprobó que **no** puede es leer los
  atributos de una suscripción (`sns:GetSubscriptionAttributes`), que es por qué
  `just alerts-prod` mira la lista del tema y no la suscripción. Se descubre el
  resto al desplegar, no adivinando.

## Últimos trabajos terminados

- 2026-09-03 — **Las alarmas de producción ya le llegan a alguien.** El tema
  estaba sin un solo suscriptor y CloudFormation lo daba por creado, así que
  redesplegar no lo arreglaba. Suscrito y confirmado desde el buzón; comprobado
  contra AWS que la suscripción es real y que las tres alarmas de mensajes
  fallidos apuntan a ese tema y están en OK. `just alerts-prod` / `alerts-dev`
  responden quién recibe, preguntándole al tema y no a CloudFormation, que es lo
  que hacía invisible el problema.
- 2026-09-03 — **Una tarjeta se puede sacar de la cuenta equivocada, y una
  cuenta cerrada se puede volver a abrir.** Desenlazar suelta los movimientos que
  entraron por esa tarjeta —vuelven a quedar sin asignar y el saldo se recalcula—
  para que enlazarla en la cuenta correcta se los lleve. Dos operaciones nuevas,
  sus botones en la pantalla de Cuentas y el aviso que decía que reabrir «no se
  puede desde la app», que ya no es cierto.
- 2026-09-03 — **Un solo lector de cola para los tres procesos.** Recibir, borrar
  y contar vivía copiado en ingesta, comercios y finanzas, y una de las copias
  dejaba escapar el error: un mensaje que fallaba tumbaba el proceso entero y
  abandonaba a los que venían detrás en el mismo lote. Ahora el mensaje se queda
  en la cola, como ya hacía el camino de Lambda.
- 2026-09-03 — **Un correo reenviado a mano ya se lee con la plantilla del
  banco.** El banco se identificaba solo por el remitente del sobre, así que un
  reenvío hecho a mano —que sale de tu propia dirección— pasaba de largo por las
  plantillas y caía en la LLM, que rechaza un pago de tarjeta a propósito porque
  nombra dos instrumentos propios. Ahora, y solo cuando el remitente no nombra
  ningún banco, se lee el encabezado del reenvío. Los tres correos así en
  desarrollo quedaron en `pending_fallback` sin registrar nada; reenviarlos otra
  vez ya entra. 23 pruebas nuevas, incluidas las del camino automático para que
  no se mueva.
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
