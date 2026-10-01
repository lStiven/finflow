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
  se paga.

Los comercios se normalizan aparte: el texto del banco se convierte en un
comercio con nombre y categoría, y hay una pantalla para revisar y corregir.

**Estado técnico:** 73 operaciones de API en cinco contextos, seis procesos en
la nube, 2021 pruebas de Python y 377 del frontend, todas en verde. Siete
tablas: la séptima, `throttle`, cuenta los intentos contra las puertas que se
pueden adivinar y se vacía sola por TTL.
El contrato de la API y los tipos del frontend están sincronizados. Hay trabajo
sin confirmar en el árbol (desenlazar tarjeta, reabrir cuenta, el lector de
cola compartido, la paginación de notificaciones, las categorías propias, y el
contexto `alerts` entero).

**Pantallas:** veinte, y están todas menos una. Resumen, Transacciones (incluido crear,
trasladar y borrar), Cuentas (con la pantalla de financiación y su tabla de
amortización), Comercios, Reportes, Perfil, la guía para conectar el banco y las
cuatro guías, más **Facturas** desde el 2026-09-14. Las entradas del menú
anunciadas sin pantalla son dos: **Presupuestos** —que existe en `dev` y aquí
sale deshabilitada, con su «Pronto», para que se vea que viene— y
**Configuración**, que no existe en ninguna rama.
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

## Lo publicado, medido el 2026-09-20

Medido contra AWS y contra el `openapi.json` que sirve cada API, no recordado:

| | Operaciones | Estado |
|---|---|---|
| Contrato de la rama `dev` | 79 | — |
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

4. **La pantalla de Configuración**, la única anunciada sin existir en ninguna
   rama. La otra que el menú anuncia aquí, Presupuestos, ya está construida en
   `dev` y llega con la mezcla.

5. **Terminar de conectar los avisos en producción.** En desarrollo ya está
   cerrado y comprobado el 2026-09-14: los dos secretos están en SSM, el
   webhook quedó registrado y `getWebhookInfo` lo confirma entregando, sin
   errores y sin cola. Falta la misma pareja de pasos en producción, con el
   otro bot (`finflow_v2_bot`): `just secret-put /finflow/production/…` y
   `just telegram-webhook-prod <ApiUrl>/alerts/telegram/webhook`. Sin lo
   último, vincular parece funcionar y no pasa nada. Todo en
   [docs/alerts.md](docs/alerts.md).

6. **Seguir con el segundo feature: facturas y pagos recurrentes** (E2).
   Las entregas A y B están completas —declarar, ver venir, y confirmar o
   saltar el cobro a mano—. Siguen C (que se cargue solo, con ventana de
   conciliación), D (que el detector proponga) y E (avisar antes del cobro).
   El plan completo —las cinco entregas, los riesgos y los cuatro nombres que
   se parecen— está en el artefacto, no aquí.
   **Al desplegar esto, `alerts` va primero:** Financial ya publica
   `origin: "scheduled"` y un consumidor que no sepa leerlo manda el mensaje a
   la DLQ en minutos. Las dos funciones salen del mismo despliegue, así que en
   la práctica es solo no partirlo en dos.

7. **Publicar automáticamente: construido el 2026-09-30, falta encenderlo.**
   `.github/workflows/pipeline.yml`: un push a `master` despliega producción y
   uno a `dev` desarrollo, después de `check-all`, `infra-check`, `just verify`
   y todas las e2e (pantallas incluidas) contra la pila local en el runner; el
   smoke va después del despliegue y, si falla, vuelve sola a la versión
   anterior. Falta lo que solo puede hacer el dueño: el proveedor OIDC y los dos
   roles en AWS, y los entornos, variables y secretos en GitHub —la lista está
   en [docs/ci.md](docs/ci.md)—.

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

- 2026-10-01 — **Los avisos se pueden borrar, y el de un movimiento borrado
  desaparece solo.** Uno a uno o «Borrar todo», y no vuelven aunque el evento
  se reentregue. El toast se rehízo como tarjeta propia, y un error ya no
  muestra «Something went wrong!»: hay pantalla propia para sin conexión, algo
  que ya no existe y fallo nuestro. En `master` y en `dev`.
- 2026-10-01 — **«Sin cuenta asignada» ya dice la verdad.** Se leía de la
  huella de tarjeta y no de la cuenta donde quedó el movimiento, así que toda
  factura confirmada y todo gasto escrito a mano en una cuenta salía como «sin
  cuenta» (y una tarjeta sin cuenta declarada, al revés). El detalle ya nombra
  los cuatro orígenes; decía «Alerta del banco» a un cobro de factura.
- 2026-09-26 — **Un pago a otra entidad ya se puede marcar como traslado.**
  Bancolombia avisa «Pagaste $X a BANCO COMERCIAL AV VILLAS desde tu producto
  *5261»: una cuenta y una institución, nunca la tarjeta, así que entraba como
  gasto y el patrimonio quedaba mal por todo el pago. Ahora esa frase tiene
  plantilla (lee exactamente lo que leía el modelo, para no darle otra
  identidad a lo ya registrado) y el movimiento se declara traslado de tres
  formas: emparejado con el que el otro banco sí avisó, escribiendo el abono en
  una cuenta tuya, o hacia fuera de Finflow. Todo en un solo write, y todo se
  deshace. Traído a `master` desde `dev` antes que los presupuestos; aquí no
  hay rechazo por factura vinculada porque ese vínculo (E2·C) aún no existe.
  El seed trae los dos casos para probarlo en local.
- 2026-09-22 — **Las puertas que se pueden adivinar ahora se cansan.** Login,
  registro, el correo de verificación, la recuperación, el cambio de
  contraseña y el secreto del webhook de Telegram cuentan intentos por
  dirección y, donde hay cuenta, también por cuenta. La distinción que
  sostiene todo lo demás: **la cuenta es una cerradura** —cinco claves malas
  por cuarto de hora, comprobadas *antes* de verificar, que es lo único que
  impide seguir adivinando— y **la dirección es un freno** —veinte por
  minuto, que se suelta solo—. Entrar bien no gasta nada y perdona lo
  anterior, así que una casa o una oficina detrás de una sola IP nunca paga
  por usar la app. Los contadores viven en DynamoDB con TTL, porque en Lambda
  un contador en memoria no cuenta nada; y **fallan abiertos y rápidos**: sin
  tabla, un login sigue tardando lo que tarda bcrypt en vez de colgarse un
  minuto. Dos falsos positivos los encontró la propia suite: la primera
  versión del freno rechazaba una clave *correcta* durante quince minutos
  tras una ráfaga ajena, y el webhook rechazaba a Telegram con el secreto
  bueno cuando alguien había gastado la puerta.
- 2026-09-21 — **Desplegar sin cambios de backend ya no es un error.**
  `fail_on_empty_changeset = false` en los dos entornos de
  `infra/samconfig.toml`. La imagen solo lleva `pyproject.toml`, `uv.lock` y
  `src/personal_finance`, así que una rama de frontend construye la imagen que
  ya está desplegada y SAM lo reportaba como avería. Medido: las seis imágenes
  de esta rama y las que corre producción comparten id `86fb2af2a950`.
