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
**2026-09-30:** las cinco e2e (`just e2e`) y las 21 pantallas a 390 y 1280 px,
en `dev` y en `master`, sin errores ni desbordes.

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
  banco, se registra a mano el lado que sí se conoce — o, si el correo ya
  llegó como gasto («Pagaste $X a BANCO COMERCIAL AV VILLAS»), se **marca
  como traslado** desde el propio movimiento: se empareja con el ingreso que
  el otro banco sí avisó, o la app escribe el abono en la cuenta que elijas.
  La pantalla lo propone cuando el banco del correo es el de una cuenta tuya,
  y se puede deshacer.
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

- **Los movimientos se exportan a CSV o Excel.** Desde Transacciones se elige
  el periodo, gastos o ingresos, la cuenta, la categoría y si entran los
  traslados; sale el archivo con todas las páginas, no solo la que se ve. Ninguna
  celda se vuelve fórmula aunque el banco escriba `=…`.

- **Lo que llega a Telegram se ve también en la app**, tenga o no canal: cada
  movimiento aparece como notificación flotante mientras la pestaña está
  abierta (se consulta cada 15 s) y una campana lista los últimos. Bajo una
  compra va **lo que queda de cada presupuesto que la cubre** —solo si alguno la
  cubre—, igual en Telegram. Y **cada lunes**, el resumen de la semana contra la
  semana normal de uno mismo, a Telegram y a la campana.

Los comercios se normalizan aparte: el texto del banco se convierte en un
comercio con nombre y categoría, y hay una pantalla para revisar y corregir.

**Estado técnico:** 88 operaciones de API en cinco contextos, siete procesos en
la nube, 2455 pruebas de Python y 473 del frontend, todas en verde, y cinco
e2e en el navegador (`just e2e`). El contrato de la API y los tipos del
frontend están sincronizados.

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

## Lo publicado, medido el 2026-09-30

| | Código | Estado |
|---|---|---|
| Desarrollo — API y web | `dev` (5474b9c), 88 operaciones | publicado el 2026-09-30, `UPDATE_COMPLETE` |
| Producción — API y web | 43 operaciones, web anterior al 2 de septiembre | sin cambios |
| Rama `master` | exportar + avisos en la app + resumen semanal, **sin presupuestos** | lista para producción, sin publicar |

`master` lleva la exportación y las notificaciones pero no los presupuestos, la
mesada ni las facturas propuestas: esos siguen solo en `dev`. Por eso en
producción el aviso saldrá **sin** la línea de presupuesto; se enciende sola el
día que presupuestos llegue a `master`.

**Publicar la web ya no está trabado:** `wrangler` tiene sesión (OAuth) con la
cuenta de Cloudflare. Y `just deploy-*` **pide confirmar el changeset** en la
terminal (`confirm_changeset = true`): sin nadie que responda aborta sin tocar
nada, y el changeset queda creado para revisarlo y ejecutarlo.

| | Nombre | Buzón | Revisa cada |
|---|---|---|---|
| producción | `finflow` | `finflowingest@gmail.com` | 1 minuto |
| desarrollo | `finflow-dev` | `finflowdevelopment@gmail.com` | 5 minutos |

Producción ya tiene un usuario real. Los dos entornos existen completos —
tablas, secretos, respaldo puntual, alarmas — pero viven en la misma cuenta de
AWS (ver Trabas).

## Lo siguiente, en orden

1. **Publicar `master` en producción.** Está lista y probada: `just deploy-prod`
   (confirmar el changeset), `just web-publish` en la misma sentada —una web
   vieja frente a una API nueva rompe la pantalla, ya pasó el 2026-09-01— y
   `just smoke-prod`. Trae una función nueva, `WeeklySummaryFunction`, que sale
   los lunes a las 8:00. Después, borrar a mano el índice viejo `by_user` de la
   tabla de notificaciones. Presupuestos, mesada y facturas propuestas siguen
   solo en `dev` hasta que se decida llevarlos.

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

7. **Publicar automáticamente: en marcha, falta el primer despliegue en verde.**
   Un push a `dev` valida y despliega desarrollo; un PR a `master` solo valida;
   un merge a `master` valida, corre las e2e y despliega producción. Los roles
   OIDC existen y se asumen (el `sub` de GitHub lleva los ids numéricos del
   repo). Pendiente: que `sam deploy`, la web y el smoke pasen en el runner
   —el config de SAM ya va con ruta absoluta—, ver por qué la e2e se pasó de
   tiempo en GitHub cuando corría en `dev` (en local pasa), y la regla de
   protección de `master`. Detalle en [docs/ci.md](docs/ci.md).

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

- 2026-10-05 — **El pipeline pasa validaciones y e2e en un runner limpio**:
  el `.env` de CI ahora trae el secreto de tokens y la dirección del buzón.
- 2026-10-04 — **Confirmar el reenvío ya no miente.** Se marcaba confirmado
  con un 200 aunque Gmail siguiera pendiente, y un enlace en `mail.google.com`
  se perdía sin rastro. La guía del reenvío se rehízo en dos partes —autorizar
  la dirección, crear el filtro— y el paso ya no se cierra con la sola
  confirmación. Que Gmail acepte `@dominio` en «De» no lo documenta Google: la
  guía hace comprobarlo buscando antes de guardar.
- 2026-10-01 — **Los avisos se pueden borrar, y el de un movimiento borrado
  desaparece solo.** Uno a uno o «Borrar todo», y no vuelven aunque el evento
  se reentregue. El toast se rehízo como tarjeta propia, y un error ya no
  muestra «Something went wrong!»: hay pantalla propia para sin conexión, algo
  que ya no existe y fallo nuestro. En `master` y en `dev`.
- 2026-10-01 — **«Sin cuenta asignada» ya dice la verdad.** Se leía de la
  huella de tarjeta y no de la cuenta donde quedó el movimiento; el detalle ya
  nombra los cuatro orígenes.
- 2026-09-30 — **`master` lista para producción** con la exportación y los
  avisos en la app, sin presupuestos (no existen ahí); `dev` publicada entera
  en desarrollo, API y web.
