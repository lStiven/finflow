# Progreso

Dónde está el trabajo hoy: qué está hecho, qué sigue, qué está trabado.
Se lee al empezar una sesión y se actualiza al terminar un trabajo.

No se narra aquí lo que cambió (eso ya lo guarda git) ni *por qué* algo es como
es (eso vive en [docs/decisions.md](docs/decisions.md), que **no** se lee al
arrancar: se busca dentro cuando hay una duda concreta).

Última verificación contra el código y contra AWS: **2026-09-04**.
La verificación local de los avisos por Telegram: **2026-09-14**.
Los presupuestos se comprobaron contra la pila local el **2026-09-18**, con
`just e2e-budgets`, `just check-all` y en el navegador, después de rehacerlos
sobre el modelo de alcance.

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
  se paga. Al confirmar, la pantalla recuerda que eso **escribe un movimiento
  nuevo** y que si lo que uno quería era apartar la plata del mes, eso son los
  Presupuestos.

- **Y se pueden cobrar solas, factura por factura.** Apagado hasta que alguien
  lo encienda. Una factura armada **no cobra el día que vence**: espera a que
  se cierre la ventana de cinco días, porque un cobro domiciliado lo presenta
  un negocio y lo asienta un banco, y escribir antes es apuntar dos veces la
  misma plata. Y **no cobra hacia atrás**: se guarda desde qué día se armó, así
  que encenderlo hoy no toca el cobro que llevaba una semana vencido. Antes de
  escribir nada, la app **mira si el movimiento ya está**: si en el historial
  hay uno que cuadra —el mismo comercio, más o menos el mismo monto, cerca del
  día—, el cobro queda pagado **por ese movimiento** y no se escribe nada; eso
  pasa con todas las facturas activas, armadas o no, y es lo que evita cobrar
  dos veces cuando el banco sí avisó. Si hay dos que podrían serlo, o uno que
  solo se parece, no decide nadie: la pantalla lo propone y basta un toque
  para enlazarlo —o para enlazar a mano cualquier movimiento con cualquier
  cobro—. Desenlazar **no borra nada**: el movimiento es del banco y se queda
  donde está; deshacer un cobro automático sí borra el que escribió la app, y
  además lo marca como saltado para que no vuelva solo esa misma tarde.

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

- **Y dice cuánto queda para gastar.** Quien diga cuánto espera que entre
  este mes —y cuánto quiere guardar— ve arriba del Resumen un solo número:
  lo declarado, menos lo que ya se gastó, menos lo que las facturas todavía
  deben. **Una factura pagada se descuenta una sola vez**: al confirmarla sale
  de lo que se debe y entra en lo gastado, y el número no se mueve. La tarjeta
  enseña la resta completa —nadie cree un número que no puede comprobar— y
  reparte lo que queda entre los días que faltan. Sin declarar nada **no hay
  tarjeta**, no un cero: «no me has dicho cómo es tu mes» y «no te queda nada»
  son cosas distintas. La nómina que el detector ya reconoció se ofrece para
  rellenar el ingreso de un toque.

- **Y se le puede poner tope a lo que quieras.** En `/presupuestos` se declara
  un tope con su nombre y lo que vigila: **todo el mes** (sin elegir ninguna
  categoría, que es el más fácil de empezar), una categoría, o varias juntas
  —«Salidas» son restaurantes y bares y domicilios—. La pantalla enseña una
  barra contra lo que llevas gastado: verde, ámbar al 80 % —el punto lo eliges
  tú— y rojo al pasarlo. **Poner un tope no mueve ningún saldo y no bloquea
  nada**: informa, y decides tú. Cada tope se repite todos los meses o vale
  solo para uno concreto, y los dos **conviven**: el de diciembre se lee al
  lado del de siempre, no en su lugar, porque dos topes pueden solaparse a
  propósito. Un tope se corrige entero —nombre, techo, alcance, aviso— sin
  perder su identidad. Un tope cuyas categorías se borraron después no rompe
  la pantalla: sale marcado y se puede quitar, y si solo desapareció una de
  varias lo dice sin retirar el tope. Y donde más se te va sin tope, la
  pantalla lo ofrece. En Resumen queda un resumen —«2 de 4 en verde»— debajo
  de las cuentas.

Los comercios se normalizan aparte: el texto del banco se convierte en un
comercio con nombre y categoría, y hay una pantalla para revisar y corregir.

**Estado técnico:** 83 operaciones de API en cinco contextos, seis procesos en
la nube, 2217 pruebas de Python y 416 del frontend, todas en verde salvo la
de la mesada que se cae cinco horas al día (ver Huecos conocidos).
El contrato de la API y los tipos del frontend están sincronizados. Hay trabajo
sin confirmar en el árbol (desenlazar tarjeta, reabrir cuenta, el lector de
cola compartido, la paginación de notificaciones, las categorías propias, y el
contexto `alerts` entero).

**Pantallas:** veintiuna, y están todas menos una. Resumen, Transacciones (incluido crear,
trasladar y borrar), Cuentas (con la pantalla de financiación y su tabla de
amortización), Comercios, Reportes, Perfil, la guía para conectar el banco y las
cuatro guías, más **Facturas** desde el 2026-09-14 y **Presupuestos** desde el
2026-09-18. La única entrada del menú anunciada sin pantalla es
**Configuración**.
Las dieciocho se revisaron una por una en un navegador el 2026-09-03, y las
cifras se comprobaron contra la API. La diecinueve, la guía de avisos, se
revisó el 2026-09-14. Facturas se revisó en el navegador el 2026-09-14, con
`just e2e-bills` y a ojo. Presupuestos, el 2026-09-18, con `just e2e-budgets`
y a 320, 390 y 1280 px.

**En cualquier pantalla, del teléfono más pequeño al monitor.** Comprobado a
320, 360, 390, 430, 768, 1024 y 1440 px, y con el teléfono acostado: ninguna
pantalla se va de lado, y a todas las secciones se llega sin escribir la
dirección. Abajo de 1024 px la barra inferior lleva Resumen, Transacciones,
Cuentas y **Más**, que abre el resto —Reportes, Comercios, Guías, la cuenta y
cerrar sesión—; de 1024 para arriba, la columna de la izquierda de siempre.

## Lo publicado, medido el 2026-09-20

Medido contra AWS y contra el `openapi.json` que sirve cada API, no recordado:

| | Operaciones | Estado |
|---|---|---|
| API producción | 83 operaciones | 43 — le faltan préstamos, inversiones, borrar movimiento, desenlazar tarjeta, reabrir cuenta, las categorías propias, los avisos, las facturas y los presupuestos |
| API desarrollo | 83 operaciones | 43 — igual que producción |
| Web (ambas) | pestaña Traslado, borrar, financiación, desenlazar, reabrir, categorías propias, avisos, facturas, presupuestos | ninguna |
| Contrato de la rama `dev` | 83 | — |
| API desarrollo (`finflow-dev`) | 75 | `UPDATE_COMPLETE` 2026-09-15 |
| API producción (`finflow`) | 70 | `UPDATE_COMPLETE` 2026-09-15 |
| Web producción (`finflow-apk.pages.dev`) | — | responde 200 |
| Web desarrollo (`finflow-dev-2tc.pages.dev`) | — | responde 200 |

El despliegue del 2026-09-15 sí entró: las seis funciones están en pie en
producción, `AlertsFunction` incluida, así que **el hueco de
`lambda:PutFunctionConcurrency` está resuelto** y ya no es una traba.

Lo que le falta a producción son las nueve operaciones de lo último: la
mesada (`/financial/allowance`), los presupuestos (`/financial/budgets`), el
plan (`/financial/plan`) y las facturas propuestas (`/financial/recurring`).
Son exactamente los commits que `dev` tiene y esta rama no.

**Publicar la web sí está trabado**, y por el contenedor, no por Cloudflare:
`wrangler` no tiene credenciales aquí (`wrangler whoami` dice que no), y
`wrangler login` no puede terminar porque su callback OAuth escucha en
`localhost:8976` y el DevContainer sólo publica 5173 y 8000. Se resuelve con
un API token de Cloudflare en `CLOUDFLARE_API_TOKEN`, que no abre navegador.

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

6. **Lo que queda de los features en curso.** De las facturas (E2) están
   A, B, C y D —declarar, confirmar o saltar a mano, que se cobren solas con
   su ventana de conciliación, y que el detector proponga—; falta solo **E**
   (avisar *antes* del cobro), **aplazada a propósito**. El tercer
   feature (E3, el disponible del mes) está entregado, y el cuarto (E4, los
   presupuestos) también salvo su aviso por Telegram — que **no depende de
   E1**, como el plan creía, sino del punto 3 de esta misma lista: un
   movimiento no tiene categoría cuando se registra, así que anunciar que se
   cruzó un tope necesita el recorrido de usuarios que hoy no existe.
   **El cuarto (E4) se rehízo el 2026-09-18**: un presupuesto pasó de ser una
   categoría a ser un *alcance* con id propio, siguiendo el modelo de
   TimelyBills. Quedan tres iteraciones de eso: periodos libres (semanal,
   anual, un rango para un viaje), arrastre del sobrante al mes siguiente, y
   las alertas. La de alertas tiene un atajo que este archivo no había visto:
   **un tope sobre todo el mes no necesita categoría**, así que ese sí se
   puede evaluar al escribir el movimiento, sin el recorrido de usuarios del
   punto 3. Los de categoría siguen esperándolo.
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

- **Una prueba de la mesada se cae cinco horas al día.**
  `test_it_answers_for_the_calendar_month_and_counts_today` compara
  `days_left` contra un `today()` que el propio test calcula en UTC, mientras
  `allowance.py` lo calcula en `America/Bogota` (`today_in(zone)`). Entre las
  00:00 y las 05:00 UTC —19:00 a 24:00 en Bogotá— las dos fechas no coinciden
  y `just prepare` se pone rojo sin que nada esté mal en la aplicación. El
  arreglo es una línea en el helper del test.
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
  resto al desplegar, no adivinando. El 2026-09-15 salió
  `lambda:PutFunctionConcurrency`, que faltaba en producción y mandó el
  despliegue entero a `ROLLBACK` al crear `AlertsFunction`; **se concedió, y el
  2026-09-20 se comprobó que la función está creada y el stack en
  `UPDATE_COMPLETE`**. Queda la lección, no la traba: un permiso que falta no
  se ve hasta que CloudFormation llama a esa API a mitad del despliegue. Se
  comprueba sin desplegar, pidiendo la acción sobre una función que no existe:
  `ResourceNotFound` es permiso, `AccessDenied` es que falta.
- **`infra/iam/finflow-deploy-policy.json` no es lo que hay adjunto.** Medido el
  2026-09-15: los dos usuarios pueden `lambda:ListFunctions`, que el archivo no
  concede en ninguna parte, y producción no puede algo que el archivo sí. Es una
  política de referencia, no un espejo — arreglar el archivo no arregla la
  cuenta.

## Últimos trabajos terminados

- 2026-09-21 — **Una factura ya se cobra sola, con red debajo.** Entrega C del
  segundo feature, y la red es la mitad que importa: **antes de escribir nada
  se mira el historial**, y si hay un movimiento que cuadra, el cobro queda
  pagado *por ese movimiento* sin escribir ninguno. Eso pasa con toda factura
  activa, armada o no —reconocer plata que ya está no es actuar por nadie— y
  es lo único que separa esta feature de causar el doble conteo que existe
  para quitar. Lo automático espera a que **se cierre la ventana de cinco
  días**, no a la gracia de tres: lo primero pregunta «¿es esto el gimnasio?»
  y lo decide un banco; lo segundo pregunta «¿va tarde?» y lo decide el dueño.
  Armarlo **no toca el pasado** (se guarda desde cuándo) y dos candidatos no
  enlazan nada: elegir sería adivinar de quién es la plata. También se enlaza
  y se desenlaza a mano, y desenlazar no borra el movimiento del banco.
  Comprobado en el navegador contra la pila real (`just e2e-bills`): conciliar
  no escribió ni una fila. La revisión encontró cinco cosas reales, dos de
  ellas dinero: confirmar no miraba si un movimiento ya respondía por el cobro
  —el doble conteo, desde el otro lado— y una semanal podía cobrarse sola un
  cobro que un movimiento entre dos ventanas ya había pagado.
- 2026-09-21 — **`just up` ya abre en el navegador, en cualquier entorno y sin
  flags.** `DEFAULT_API_HOST` pasa a `0.0.0.0` en `scripts/run_stack.py`:
  dentro de un DevContainer el loopback es una interfaz distinta de aquella a
  la que Docker entrega el puerto publicado, así que el default anterior hacía
  que `http://localhost:8000/docs` no respondiera desde Windows. Era un flag
  que había que recordar y se olvidaba. Quien quiera lo de antes,
  `--api-host 127.0.0.1`; y si preocupa la red local, lo que se estrecha es el
  *publish* del `devcontainer.json`, no el bind. Los dos guías que mandaban
  escribir el flag ya no lo hacen.
- 2026-09-18 — **Un presupuesto ya no es una categoría: es un alcance.**
  Rehecho el módulo entero sobre el modelo de TimelyBills, primera de cuatro
  iteraciones. Un tope tiene **id propio, nombre e icono**, y vigila lo que se
  le diga: todo el mes sin elegir nada, una categoría o hasta veinte, y
  opcionalmente solo unas cuentas. Vacío significa *todas* en los dos ejes —un
  campo en vez de dos banderas que pueden contradecirse—. Eso **revierte a
  propósito** la identidad anterior («dos topes sobre la misma categoría son
  uno declarado dos veces»), que solo se sostenía mientras un tope vigilaba una
  categoría: «Salidas» y «Restaurantes» se solapan porque alguien lo quiso. Con
  ella se cae el **tapado**: el tope de diciembre ya no esconde al de siempre,
  los dos gobiernan el mes y los dos se muestran. A cambio aparece lo que antes
  era imposible: **corregir un tope entero** sin perder su fila. El endpoint
  pasó de `PUT /budgets` a `POST /budgets` + `PUT|DELETE /budgets/{id}`, y no
  hizo falta migrar nada porque los presupuestos **nunca se publicaron** — el
  atraso de despliegue que este archivo llama el problema más grande es lo
  único que abarató esto. La prueba de integración se ganó el sueldo: cazó que
  `AccountId` no define `__str__`, así que el alcance por cuenta se guardaba
  como el `repr` del dataclass; un doble en memoria no lo ve nunca. Y mirar la
  pantalla encontró lo que ningún test vio: la tarjeta «Todo el mes» repetía el
  mismo texto como título y subtítulo. Lo que **no** cambió: un tope sigue sin
  mover un peso, y lo comprueba el mismo e2e. Faltan las otras tres
  iteraciones —periodos libres, rollover, y alertas con nombre e icono por
  cuenta—.
- 2026-09-18 — **Ya se le puede poner tope a una categoría.**
  Cuarto feature (E4), en su propia pantalla `/presupuestos` y no dentro de
  Resumen: el plan pedía el semáforo en el desglose de Resumen, y esa pantalla
  responde otras cuatro preguntas primero. En Resumen quedó solo un resumen,
  después de las cuentas — y el disponible de E3 bajó ahí también, que era lo
  que se pedía. **El tope se repite o vale para un mes**, y el del mes tapa al
  de siempre: son dos filas y no una con una excepción encima, así que quitar
  la de diciembre deja la de siempre intacta. Lo que **no** se entregó es el
  aviso por Telegram, y el porqué es lo que hay que llevarse: un movimiento
  **no tiene categoría cuando se registra** —Financial guarda el texto del
  banco y lo resuelve al leer, que es lo que hace que corregir un comercio
  arregle el pasado—, así que nada en la escritura sabe a qué tope pertenece
  una compra. Calcularlo al abrir la app es justo lo que este proyecto ya
  decidió que no se avisa, con los devengos. Por eso el cruce **no se guarda
  ni se anuncia: se deriva**, como «pagado» se deriva de la fila del ledger.
  Las dos revisiones encontraron ocho cosas, la peor de ellas que el
  interruptor «se repite cada mes» dejaba mover la identidad del tope al
  editarlo: guardar parecía no hacer nada y dejaba una fila inalcanzable
  detrás. Y mirar la pantalla encontró lo que el código no podía: el semáforo
  tenía un solo color —`accent` es magenta y `outgoing` es rojo— bajo una
  tarjeta que dice «1 de 3 en verde», y `bg-mid` no existe, así que la barra
  ámbar del disponible llevaba sin pintarse desde E3.
- 2026-09-15 — **La app ya dice cuánto queda para gastar.**
  Tercer feature (E3). Se declara el mes —cuánto esperas que entre, cuánto
  quieres guardar— y arriba del Resumen aparece un número con su resta a la
  vista. Lo delicado no es la aritmética sino **cuál de las dos cifras de las
  facturas se resta**: se resta lo que *aún se debe*, nunca lo que el mes
  cuesta, porque un cobro ya confirmado está en lo gastado —es una fila del
  ledger— y contarlo también como compromiso lo descontaría dos veces. Un e2e
  lo comprueba contra la pila real: declara su propia factura, la confirma y
  exige que el número **no se mueva**. Sin plan declarado los dos endpoints
  responden 404 y la tarjeta no existe: un cero ahí se lee como «no te queda
  nada». El plan se reemplaza entero y nunca se fusiona, para que una meta de
  ahorro no sobreviva al ingreso contra el que se fijó. La revisión encontró
  cinco cosas reales, entre ellas que teclear «0» en «quieres guardar» se
  rechazaba y que un 5xx de la tarjeta se llevaba por delante todo el panel.
