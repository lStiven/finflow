# Progreso

Dónde está el trabajo hoy: qué está hecho, qué sigue, qué está trabado.
Se lee al empezar una sesión y se actualiza al terminar un trabajo.

No se narra aquí lo que cambió (eso ya lo guarda git) ni *por qué* algo es como
es (eso vive en [docs/decisions.md](docs/decisions.md), que **no** se lee al
arrancar: se busca dentro cuando hay una duda concreta).

Última verificación contra el código y contra AWS: **2026-09-04**.
La verificación local de los avisos por Telegram: **2026-09-14**.

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
- **Los avisos salen de la app.** Quien quiera conecta su Telegram desde
  Perfil con un toque —se abre el bot, se pulsa Empezar, y listo: no hay que
  buscar ningún identificador ni teclear ningún código— y a partir de ahí cada
  movimiento le llega al teléfono segundos después, venga del correo del banco
  o escrito a mano. Se puede apagar, y se le puede poner un mínimo para que un
  café no gaste la atención que necesita un cargo grande. Los intereses que la
  propia aplicación calcula no se avisan: llegan de a cuatro cuando alguien
  abre la pantalla del crédito, y avisarlos es lo que hace que se apaguen los
  avisos de verdad.
- **Las categorías se pueden inventar, corregir y borrar.** Las dieciséis que
  trae la aplicación las ve todo el mundo; encima de esas, cada quien escribe
  las suyas (corto: máximo 24 caracteres, porque se leen en una lista y en una
  gráfica). Se administran en un panel plegable dentro de Comercios. Cambiarle
  el nombre a una no mueve nada —el nombre y la identidad son cosas distintas—
  y borrarla devuelve sus comercios a «Sin categoría», diciendo antes cuántos
  son. Al escribir un movimiento a mano se le puede poner la categoría ahí
  mismo —y crear una sin salirse del formulario—, y eso además le crea el
  comercio, que antes no pasaba nunca. El modelo también clasifica en las
  categorías propias.
- **Las facturas se declaran y se confirman.** Un gasto domiciliado que el
  banco ya no anuncia —el gimnasio, el arriendo, el streaming— se declara una
  vez y la app lo proyecta sobre el calendario. Declararlo **no mueve ningún
  saldo**; marcarlo como pagado sí: escribe el movimiento, mueve la cuenta, lo
  clasifica en la categoría de la factura y avisa por Telegram como cualquier
  otro gasto. Marcarlo dos veces lo cobra una sola vez —la identidad del cobro
  sale de la factura y del periodo, así que el segundo intento lo rechaza la
  tabla— y se puede deshacer, que borra el movimiento y devuelve la plata. El
  mes que un cobro no llegó se salta, y eso no escribe nada. La pantalla lee
  dos cifras: lo que cuesta el mes y lo que falta por pagar. Un ingreso
  declarado —la nómina— usa los mismos botones con otras palabras: llega, no
  se paga.

- **Y la app propone las que uno no declaró.** Mira los últimos dos años de
  movimientos y, cuando algo se repite —el mismo comercio, el mismo día del
  mes, tres veces o más—, lo ofrece en `/facturas` con qué tan seguro está y
  con la evidencia a la vista («4 cobros, ninguno faltó»). Aceptar es declarar
  la factura con esas cifras: **el detector nunca declara ni cobra nada por su
  cuenta**. No propone lo que la propia app escribió (los intereses de un
  crédito, un cobro de factura ya confirmado), ni los traslados, ni lo que
  lleva dos cobros sin aparecer —una suscripción cancelada que sigue
  recordándose es peor que no tener detector—. La nómina sí se detecta, con su
  dirección, pero no se ofrece: E3 la necesita, la pantalla no.

Los comercios se normalizan aparte: el texto del banco se convierte en un
comercio con nombre y categoría, y hay una pantalla para revisar y corregir.

**Estado técnico:** 71 operaciones de API en cinco contextos, seis procesos en
la nube, 1943 pruebas de Python y 364 del frontend, todas en verde.
El contrato de la API y los tipos del frontend están sincronizados. Hay trabajo
sin confirmar en el árbol (desenlazar tarjeta, reabrir cuenta, el lector de
cola compartido, la paginación de notificaciones, las categorías propias, y el
contexto `alerts` entero).

**Pantallas:** veinte, y están todas menos una. Resumen, Transacciones (incluido crear,
trasladar y borrar), Cuentas (con la pantalla de financiación y su tabla de
amortización), Comercios, Reportes, Perfil, la guía para conectar el banco y las
cuatro guías, más **Facturas** desde el 2026-09-14. La única entrada del menú
anunciada sin pantalla es **Configuración**.
Las dieciocho se revisaron una por una en un navegador el 2026-09-03, y las
cifras se comprobaron contra la API. La diecinueve, la guía de avisos, se
revisó el 2026-09-14. Facturas se revisó en el navegador el 2026-09-14, con
`just e2e-bills` y a ojo.

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
| API producción | 71 operaciones | 43 — le faltan préstamos, inversiones, borrar movimiento, desenlazar tarjeta, reabrir cuenta, las categorías propias, los avisos y las facturas |
| API desarrollo | 71 operaciones | 43 — igual que producción |
| Web (ambas) | pestaña Traslado, borrar, financiación, desenlazar, reabrir, categorías propias, avisos, facturas | ninguna |

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
   `just web-publish` y `just smoke-prod`. Desplegar ya aprovisiona primero,
   así que el índice nuevo de las notificaciones queda antes que el código que
   lo consulta. Cuando el despliegue esté arriba, borrar a mano el índice viejo
   `by_user` de la tabla de notificaciones: ya no lo consulta nadie y se sigue
   pagando.

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

5. **Terminar de conectar los avisos en producción.** En desarrollo ya está
   cerrado y comprobado el 2026-09-14: los dos secretos están en SSM, el
   webhook quedó registrado y `getWebhookInfo` lo confirma entregando, sin
   errores y sin cola. Falta la misma pareja de pasos en producción, con el
   otro bot (`finflow_v2_bot`): `just secret-put /finflow/production/…` y
   `just telegram-webhook-prod <ApiUrl>/alerts/telegram/webhook`. Sin lo
   último, vincular parece funcionar y no pasa nada. Todo en
   [docs/alerts.md](docs/alerts.md).

6. **Seguir con el segundo feature: facturas y pagos recurrentes** (E2).
   Las entregas A, B y D están completas —declarar, ver venir, confirmar o
   saltar el cobro a mano, y que el detector proponga—. Siguen C (que se
   cargue solo, con ventana de conciliación) y E (avisar antes del cobro).
   El plan completo —las cinco entregas, los riesgos y los cuatro nombres que
   se parecen— está en el artefacto, no aquí.
   **Al desplegar esto, `alerts` va primero:** Financial ya publica
   `origin: "scheduled"` y un consumidor que no sepa leerlo manda el mensaje a
   la DLQ en minutos. Las dos funciones salen del mismo despliegue, así que en
   la práctica es solo no partirlo en dos.

7. **Publicar automáticamente.** Hoy todo se construye y se despliega a mano
   desde el contenedor. Nada está sin probar, pero un arreglo puede quedarse
   olvidado en el computador mientras producción sigue vieja — que es exactamente
   lo que está pasando ahora mismo (punto 1).

## Huecos conocidos, sin urgencia

- **La hora de los avisos es la misma para todo el mundo.** No existe zona
  horaria por usuario en ninguna parte del proyecto, así que el «13/09 04:46
  pm» de un aviso se calcula con una sola (`America/Bogota`). Deja de servir el
  día que alguien lo use desde otro huso.
- **Falta decidir si los comercios son de cada usuario o compartidos.** Hoy son
  de cada uno. Compartirlos coincidiría con la intuición, pero los nombres y las
  categorías son decisiones personales, y las veces que alguien visitó un negocio
  se filtrarían a otro.
- **A un préstamo se le puede poner cupo, y no se ve en ninguna parte.** El
  formulario lo ofrece porque el backend lo acepta para cualquier deuda, pero la
  barra de «disponible» solo se dibuja en una tarjeta. Es un dato que se pide y
  no se usa.
- **Lulo tiene plantilla a medias**: se construyó con cuatro alertas, así que una
  compra con tarjeta, un retiro o una comisión en Lulo todavía van al modelo.
- **Falta comprobar el filtro de autorizaciones con correos reales.** Una
  autorización y su cobro son dos correos distintos, y la regla que descarta la
  primera se escribió sin tener uno a la mano.
- **Una inversión ya vencida sigue acumulando en la pantalla.** El cálculo de
  «intereses pendientes» ignora la fecha de vencimiento, así que un CDT que
  venció hace un año muestra un rendimiento que crece cada mes. El registro de
  los cortes sí respeta el vencimiento; es solo lo proyectado lo que miente.
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
- 2026-09-15 — **La app propone las facturas que uno no declaró.**
  Entrega D del segundo feature. Lee el historial, agrupa por el comercio
  atribuido —no por el texto, que el mismo gimnasio llega como `PAGO GYM SA`
  y `GYMSA*BOG`— y decide la cadencia por el calendario y no por los días:
  Netflix cobra el 15 con brechas de 30, 31 y 31, y medir días la llamaría
  irregular. **No escribe nada**: aceptar una sugerencia es declarar la
  factura por el mismo endpoint del formulario. Construirlo corrigió dos
  suposiciones del plan. La ventana de trece meses **no podía funcionar**: con
  tres apariciones como mínimo, trece meses caben dos cobros anuales, así que
  lo anual no se habría sugerido nunca —en silencio, porque «no es una serie»
  y «no alcanza la ventana» se ven igual desde afuera—; son veinticinco meses.
  Y una subida de precio no es ruido: 16.900 tres veces y luego 19.900 se lee
  como el precio nuevo, no como una serie variable que predice el viejo. La
  revisión encontró las dos, más una colisión de claves entre monedas y un
  emparejamiento que fallaba justo para las facturas que esta pantalla crea.
  `just seed` deja dos series para mirarlas, y `just e2e-bills` acepta una en
  el navegador y comprueba que no movió un peso.
- 2026-09-14 — **Una factura ya se puede pagar, y eso sí es plata.**
  Entrega B del segundo feature. «Pagado» escribe el movimiento por el mismo
  caso de uso que respalda el movimiento a mano, así que el saldo, el comercio,
  el gasto del mes y el aviso por Telegram vienen puestos. **Pagar dos veces
  cobra una:** la identidad del cobro sale de la factura y del periodo —nunca
  del monto ni del día—, así que el segundo intento lo rechaza la escritura
  condicional de la tabla y no un `if`. «Pagado» **no se guarda en ninguna
  parte**: se lee de la fila del ledger, de modo que borrar el movimiento
  despaga el cobro sin que nada tenga que acordarse de deshacer nada. Saltar sí
  se guarda, porque no hay fila que leer. Las dos cosas se deshacen. Origen
  nuevo `scheduled` —ni `manual` ni `accrual`— que `alerts` aprendió en el
  mismo cambio y que **debe desplegarse primero**. `upcoming` pasó a
  `outstanding` porque cambió de significado: ya no es «aún no vence», es «sin
  pagar». Comprobado de punta a punta contra la pila real: el e2e confirma por
  el navegador, comprueba que la cuenta se movió por exactamente lo confirmado,
  y manda una segunda confirmación por la API para ver que no se mueve nada.
- 2026-09-14 — **Se pueden declarar facturas y ver lo que viene.**
  Entrega A del segundo feature: un gasto domiciliado que el banco ya no
  anuncia por correo se declara, y la app lo proyecta sobre el calendario con
  seis cadencias —la mensual conserva el día del ancla, así que una del 31 pide
  prestado el fin de febrero y en marzo vuelve al 31—. **No escribe nada en el
  ledger**, y hay una prueba de integración que lo comprueba contra la tabla
  real. Dos cifras por moneda y nunca una: lo que cuesta el mes y lo que aún no
  vence. Una factura cuya cuenta se cerró se lee congelada, derivado de la
  cuenta y no guardado, así que reabrirla la descongela sola. La pantalla
  `/facturas` ya está —la barra del mes arriba, un mosaico de fichas donde el
  icono y el color salen de la categoría de cada factura, y los cobros en una
  línea de tiempo con el día de hoy marcado—, y con ella una
  prueba de punta a punta que el proyecto no tenía (`just e2e-bills`): conduce
  el navegador, y después de cada paso compara lo que la pantalla enseña con lo
  que el servidor guardó. Lee saldos y patrimonio antes y después, y no pasa si
  declarar movió alguno. `just seed` deja seis facturas declaradas con su
  categoría, así que el entorno local arranca con el flujo completo.
- 2026-09-14 — **Un movimiento escrito a mano ya avisa.** Financial publica
  `bank: ""` cuando no hay banco que nombrar —lo normal en un gasto a mano— y
  el consumidor de avisos lo exigía no vacío: cada uno de esos movimientos se
  descartaba en silencio con un «malformed payload» que no decía qué campo. El
  mensaje ya decía «Tu banco» para ese caso; solo la validación de entrada no
  se había enterado. La verificación contra moto del 2026-09-14 no lo vio
  porque solo ejerció el camino de la alerta bancaria. Y ese descarte ya
  nombra el campo que lo causó —`refused='bank:string_too_short'`— sin el
  valor: lo que faltaba para que el próximo se vea el mismo día.
- 2026-09-14 — **La app ya le habla a alguien fuera de su propia pantalla.**
  Contexto nuevo `alerts`: se conecta Telegram con un toque desde Perfil y cada
  movimiento llega al teléfono. Vincular es un enlace profundo con un token de
  un solo uso, no un código tecleado — 256 bits en vez de un millón de
  combinaciones, y nadie tiene que averiguar su `chat_id`. El mensaje dice solo
  lo que trae el evento; el total del mes es de E3 y el nombre bonito del
  comercio es de Merchant. La marca de entrega se escribe *después* de mandar,
  al revés que en Merchant: repetir un aviso molesta, perderlo es una compra de
  la que nadie se enteró. Verificado de punta a punta contra moto.
