# Textos de los strikes por uso indebido del modelo

Aprobados por el dueño el 2026-10-30, para la entrega (c) del tope del modelo
(ver «Limiting the model» en [decisions.md](decisions.md)). Escritos para los
usuarios de Finflow: tuteo, tono cercano. Ya dicen que **solo se suspende el
modelo**: las alertas de los bancos con plantilla se siguen leyendo. Si cambia
la lista de bancos con plantilla, cambia ese párrafo.

`{nombre}`, `{n}` y `{fecha}` se rellenan al enviar. `{canal}` todavía no:
servicio al cliente no tiene canal, así que el correo dice solo «disponible muy
pronto».

**Construido el 2026-10-31.** El texto vive en el código, y es lo que hay que
cambiar si cambia esta página:
`src/personal_finance/contexts/alerts/infrastructure/notice_copy.py` (correo y
Telegram) y `frontend/src/alerts/modelPause.ts` (banner y campana).

## Strike 1 — pausa de 24 horas

**Asunto:** Pausamos la lectura de tus correos por 24 horas

> Hola, {nombre}:
>
> En menos de una hora llegaron a tu dirección de Finflow {n} correos que no
> eran alertas de tu banco. Cada correo que Finflow no reconoce pasa por un
> lector automático, y cada lectura tiene un costo.
>
> Por eso pausamos hasta el {fecha} la lectura de los correos que Finflow no
> reconoce por sí solo. Las alertas de los bancos que ya conocemos (Bancolombia
> y Lulo) se siguen registrando solas; las demás que lleguen en este tiempo no
> se van a leer. Tu cuenta sigue funcionando igual: puedes entrar, revisar tus
> cuentas y anotar a mano lo que gastes.
>
> Lo más probable es que un filtro de reenvío esté dejando pasar de más. Revisa
> que solo reenvíe las alertas de tu banco; la guía para conectar el banco te
> muestra cómo.
>
> Finflow está para ayudarte con tu plata y queremos seguir haciéndolo, pero el
> lector es solo para alertas bancarias. Este es un primer aviso: si vuelve a
> pasar, la pausa será de 3 días.

## Strike 2 — pausa de 3 días

**Asunto:** Segundo y último aviso: pausa de 3 días

> Hola, {nombre}:
>
> Otra vez llegaron a tu dirección de Finflow correos que no eran alertas de tu
> banco ({n} en menos de una hora). Como te habíamos avisado, pausamos hasta el
> {fecha} la lectura de los correos que Finflow no reconoce por sí solo. Las
> alertas de Bancolombia y Lulo se siguen registrando.
>
> Te lo decimos claro porque queremos que sigas con nosotros: **este es el
> último aviso.** Si vuelve a pasar, Finflow dejará de leer esos correos de
> forma definitiva. Podrás seguir usando la app y anotando a mano, pero los
> movimientos de los bancos que no reconocemos ya no se registrarán solos.
>
> Antes de que termine la pausa, revisa que a Finflow solo le lleguen las
> alertas de tu banco.

## Strike 3 — definitivo

**Asunto:** Finflow dejó de leer los correos que no reconoce

> Hola, {nombre}:
>
> Después de dos avisos, siguieron llegando correos que no eran alertas
> bancarias, así que Finflow ya no leerá los correos que no reconoce por sí
> solo. Las alertas de Bancolombia y Lulo se seguirán registrando solas; las de
> otros bancos tendrás que anotarlas a mano.
>
> Tu cuenta sigue activa: tus cuentas, movimientos, presupuestos y reportes
> siguen ahí. Si crees que es un error o quieres reactivar la lectura,
> comunícate con servicio al cliente ({canal}, disponible muy pronto).
>
> Lamentamos llegar a este punto. Finflow existe para ayudarte, y para eso el
> lector tiene que usarse para lo que fue hecho.

## Aviso en la web

- **Pausa temporal:** «Pausamos hasta el {fecha} la lectura de los correos que
  Finflow no reconoce: llegaron correos que no eran de tu banco. Las alertas de
  Bancolombia y Lulo se siguen registrando; lo demás puedes anotarlo a mano.»
- **Definitivo:** «Finflow ya no lee los correos que no reconoce por sí solo.
  Las alertas de Bancolombia y Lulo se siguen registrando y tu cuenta sigue
  activa. Para reactivar la lectura, comunícate con servicio al cliente (muy
  pronto).»
