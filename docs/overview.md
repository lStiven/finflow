# Finflow — qué es y cómo funciona

Finflow es un backend de finanzas personales dirigido por eventos. Su objetivo
es que nadie tenga que registrar un gasto a mano: procesa los correos que el
banco ya envía, extrae cada movimiento, normaliza el comercio, actualiza saldos
y mantiene el patrimonio neto al día.

Se entrega como **aplicación web**, accesible desde el navegador del teléfono.
Deliberadamente no es una app nativa, así que no hay revisión de tienda de por
medio. Cuando en este repositorio se dice "la app" se habla de este software.

**Escala**: un despliegue privado para el autor y un puñado de amigos, cada uno
viendo solo sus propias finanzas. Eso justifica muchas decisiones — capa
gratuita de AWS, nada de sharding multi-tenant — pero el aislamiento por
usuario es estricto de todos modos.

## El modelo de entrada

Cada usuario **reenvía** las alertas de su banco a una dirección propia
(`algo+alias@gmail.com`) sobre **una sola cuenta de Gmail que la app misma
posee**. Un worker la revisa por IMAP; nunca se lee el buzón personal de
nadie, y no hay pantalla de consentimiento de por medio.

Esto no es un detalle de implementación, es la decisión central del producto:

- **El usuario configura el reenvío en su propio cliente de correo.** Es una
  regla de filtro ("todo lo que venga de mi banco, reenvíalo"), no algo que
  nosotros gestionamos por él.
- **La aprobación de remitentes sigue mandando.** Un correo reenviado solo se
  acepta si viene de un remitente que ese usuario aprobó explícitamente; una
  lista vacía significa *no aceptar nada*, nunca *aceptarlo todo*.
- **Nunca se lee un buzón ajeno.** La única bandeja que este sistema abre es
  la que él mismo controla, y solo para esto.

El contrato de API — cómo un usuario obtiene su dirección de reenvío y aprueba
remitentes — está en [email-forwarding.md](email-forwarding.md).

## El flujo principal

```mermaid
flowchart TD
    A["El banco envía la alerta<br/>al correo del usuario"] --> B["El cliente de correo del usuario<br/>la reenvía a su alias"]
    B --> C["Ingest worker<br/>revisa el buzón compartido por IMAP"]
    C --> D["Filtrado<br/>solo remitentes aprobados"]
    D --> E["Deduplicación<br/>escritura condicional en DynamoDB"]
    E --> F["Cola SQS"]
    F --> G["Parse worker"]
    G -->|"plantilla determinista"| H["Transacción extraída"]
    G -->|"ninguna plantilla<br/>coincidió"| I["Gemini (plan B)"]
    I --> H
    I -.->|"no pudo leerlo"| J["Se conserva<br/>pending_fallback"]
    H --> K["EventBridge<br/>TransactionExtracted"]
    K --> L["Merchant worker"]
    L --> M["Comercio canónico<br/>+ categoría"]
    K --> N["Financial worker"]
    N --> O["¿A qué cuenta pertenece?<br/>banco + instrumento + últimos cuatro"]
    O -->|"coincide, o se abre<br/>en la primera aparición"| P["Fila del ledger + saldo<br/>en una sola escritura atómica"]
    O -.->|"la alerta no nombró<br/>un instrumento usable"| Q["Se conserva<br/>sin asignar"]
    P -.->|"regla del dominio,<br/>todavía sin consulta"| R["Patrimonio neto<br/>activos − pasivos"]
```

Merchant y Financial escuchan el **mismo** evento y no se conocen entre sí:
uno decide quién es el comercio, el otro cuánto dinero se movió y de dónde.

Dos propiedades gobiernan toda la cadena:

**Primero lo determinista, siempre.** Una plantilla es barata, repetible y
auditable. El modelo de lenguaje solo se consulta cuando todas las plantillas
del banco fallaron, y lo que devuelve pasa por un esquema Pydantic y después
por los objetos de valor del dominio. Si algo no cuadra, el correo se conserva
sin parsear en vez de inventarse una transacción.

**La entrega es at-least-once.** Todo el sistema asume que el mismo mensaje
puede llegar dos veces: la identidad de una notificación se deriva del usuario
más el `Message-ID`, la deduplicación es una escritura condicional en DynamoDB,
y ningún contador se incrementa sin haber reclamado antes el `event_id` del
evento que lo provocó.

## Los contextos acotados

Cada contexto es dueño de su modelo y no importa agregados, repositorios ni
servicios de dominio de otro. La única superficie de integración permitida es
un caso de uso publicado, llamado desde un adaptador en la capa de
infraestructura de quien llama — o, entre contextos asíncronos, un evento de
integración en EventBridge.

### Ingestion

El buzón de ingesta compartido, la asignación de direcciones de reenvío,
filtrado por remitente, deduplicación, parseo y extracción. Publica un único
evento hacia afuera: `TransactionExtracted`. Todo su ciclo de vida interno
(recibido, encolado, ignorado, fallido) se queda dentro.

La dirección de reenvío de un usuario (`finflowingest+<id>@gmail.com`) es una
función pura de su `user_id` — no hay nada que asignar ni que pueda colisionar.
El `ingest worker` revisa esa única cuenta por IMAP en un bucle con espera
entre pasadas (no hay equivalente a *long polling* en IMAP), y marca cada
correo como leído solo después de haberlo entregado de forma durable — un
reintento tras una caída vuelve a leerlo, nunca lo pierde.

### Merchant

Identidad canónica de comercios, alias y clasificación. Un **comercio** es un
padre; sus **hijos** son cada grafía que un banco ha usado para él. Solo se
llega al padre a través de los hijos.

El reconocimiento tiene cuatro niveles, en orden de cuánto asumen:

1. **Huella exacta** — la misma grafía ya vista. Ahí aterriza casi todo.
2. **Root key** — la grafía sin números de tienda, colas de dirección, formas
   jurídicas ni palabras genéricas. Mismo nombre módulo ruido, mismo padre.
3. **Submarca** — la raíz extiende a otra en frontera de token
   (`EXITO EXPRESS` bajo `EXITO`). Solo para compras, nunca para
   transferencias, que nombran personas.
4. **El modelo** — solo si todo lo anterior falló. Sabe lo que ningún
   normalizador puede: que `BANCOLOMBIA NEQUI` y `NEQUI` son una empresa.

El sesgo que decide cada empate: **agrupar mal etiqueta dinero y se esconde;
no agrupar cuesta un clic que se recuerda para siempre.** Por eso los niveles
3 y 4 marcan lo que proponen como *sugerencia* revisable, y una corrección del
usuario le gana a cualquier regla de forma permanente.

### Identity

Cuentas, credenciales y autenticación. También expone la bandeja (dirección de
reenvío + remitentes aprobados) de cada usuario autenticado, delegando en los
casos de uso de Ingestion a través de un adaptador propio — nunca calcula la
dirección ella misma.

### Financial

Cuentas, transacciones, saldos, deuda y patrimonio neto. Es lo que convierte
todo lo anterior en un número que alguien puede mirar.

**Las cuentas se abren solas.** La primera vez que se ve un par (banco,
instrumento) nuevo aparece una cuenta con nombre genérico y renombrable
—`Bancolombia ••7653`— marcada para revisión, con saldo de apertura cero
porque es desconocido: esa cuenta la descubrió una alerta, no la declaró su
dueño. Para lo que nunca envía un correo por movimiento —una hipoteca, un
crédito, efectivo en un cajón— el dominio ya sabe abrir una cuenta declarada
por su dueño, con el saldo que este indique; falta el endpoint que lo exponga.

Cada cuenta tiene un **tipo** (ahorros, corriente, tarjeta de crédito,
préstamo…) del que se deriva si es **activo o pasivo**; nadie puede declarar
que una hipoteca es un activo. El patrimonio neto es la suma de activos menos
la de pasivos, y es lo que hace que 1,2M en una tarjeta *reste*: lo que esa
cuenta guarda es deuda.

**La identidad de un movimiento sale de su contenido, no del correo.** Un
banco puede anunciar una compra en dos mensajes, el mismo mensaje puede
parsearse dos veces, y SQS entrega al menos una vez: bajo las tres el dinero
se movió una sola vez. La clave se calcula sobre usuario + banco + instrumento
+ dirección + monto + hora + contraparte, y va hasheada para que ningún
importe ni contraparte quede en claro allí donde se registra un id.

**El ledger manda; el saldo es un total acumulado.** Nada en memoria decide si
un movimiento ya se vio —esa respuesta no sobrevive a un reinicio—: lo decide
una escritura condicional sobre la clave del movimiento. La fila del ledger y
el cambio de saldo salen en una única escritura atómica, así que un saldo no
puede moverse sin una fila detrás que lo explique, y el saldo se puede
recalcular reproduciendo las filas cuando se sospecha que derivó.

**Un movimiento que no encuentra cuenta se conserva sin asignar**, nunca se
adivina ni se descarta: la alerta no nombró instrumento, lo nombró sin los
últimos cuatro dígitos, o nombró uno que Financial no sabe leer. El dinero se
movió igual, y un registro que alguien puede colocar después vale más que un
fallo limpio. Es la misma filosofía que `pending_fallback` en Ingestion.

**Una cuenta se identifica por (banco, tipo de instrumento, últimos cuatro).**
Emparejar solo por banco y tipo fundiría todas las cuentas de ahorro de un
mismo banco en un saldo equivocado.

**Una autorización no es un movimiento, y se descarta antes de llegar aquí.**
Una retención de hotel y su cobro real son dos correos distintos con cuerpos
distintos, así que el sitio barato y seguro para dejar caer el primero es el
parser: nunca llega a ser un `TransactionExtracted` y Financial no lo ve. Las
plantillas deterministas solo reconocen hechos consumados —`Compraste`,
`Pagaste`, `Transferiste`, `Recibiste`— y al modelo se le dice explícitamente
que rechace cualquier cosa aprobada, retenida o en proceso. Conciliarlas
después habría exigido una heurística sobre montos y ventanas de tiempo para
deshacer algo que el texto original ya dice.

> **Lo que todavía no está.** No hay endpoints: el worker escribe cuentas,
> filas y saldos, pero nada los consulta por HTTP todavía —ni el patrimonio
> neto, que hoy es una regla del dominio y no una consulta.
> `PROGRESS.md` lleva la lista completa.

## Decisiones que conviene conocer

**El dinero nunca es un float.** Es `Decimal` en el dominio y una *cadena* en
los payloads de integración: un float JSON pierde centavos antes de que
Financial llegue a verlos.

**Los eventos de integración se construyen campo por campo.** Un evento de
dominio solo llega a EventBridge si el traductor de su propio contexto lo mapea
explícitamente. Añadir un campo a un agregado nunca puede ensanchar en silencio
lo que otros contextos reciben.

**El contenido de un correo y la salida del modelo son datos no confiables.**
Ambos se validan en el borde y se vuelven a validar en el dominio.

**Un secreto nunca se escribe en código ni en un log.** La contraseña de
aplicación de la cuenta de ingesta vive en variables de entorno, nunca en
código; las contraseñas de usuario solo cruzan hacia el almacenamiento ya
hasheadas; y la app se niega a arrancar si falta un secreto obligatorio —
incluida la dirección de ingesta misma, sin la cual nadie podría registrarse.

**El webhook de notificaciones bancarias es una costura de pruebas**, no un
camino de producto. Está montado únicamente cuando `ENVIRONMENT=local`.

## Stack

Python 3.13 (fijado), FastAPI, Pydantic v2, uv como único gestor de
dependencias, `just` para las tareas del proyecto. En AWS: DynamoDB, SQS,
EventBridge. Autenticación con bcrypt y JWT. Gemini como plan B de extracción y
clasificación. El DevContainer de VS Code es el entorno de desarrollo canónico.

---

Para levantar y probar todo esto, ver [running.md](running.md).
