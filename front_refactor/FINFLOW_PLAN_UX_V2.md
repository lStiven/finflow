# Finflow — Plan de mejora UX/UI y arquitectura frontend–backend

**Versión:** 2.0  
**Estado:** propuesta ejecutable, sujeta a auditoría del repositorio  
**Base:** overview funcional de Finflow, capturas de las vistas y acuerdo de implementación previo  
**Propósito:** mejorar la experiencia de usuario sin duplicar el onboarding de correo que Claude ya está desarrollando y sin introducir configuraciones bancarias fijas en el frontend.

> **Regla principal:** el backend es la fuente de verdad de bancos, remitentes, autorizaciones, direcciones de reenvío, estados de procesamiento y finanzas; el frontend interpreta esos datos y ofrece una experiencia comprensible. Las guías deben mostrar acciones reales, no explicaciones técnicas extensas.

## 0. Alcance, exclusiones y cómo usar este plan

### Fuera de alcance: guía de conexión bancaria ya delegada

**NO ejecutar de nuevo `UX-04 — Onboarding de conexión de correo`.** Claude ya recibió instrucciones para rediseñarla. Se considera **trabajo en curso en paralelo** y no se incluye en el esfuerzo ni en las entregas de este plan.

**SÍ se permite:** revisar el contrato backend consumido por esa guía, detectar bancos o correos hardcodeados, definir APIs comunes y coordinar una integración mínima cuando la guía existente esté lista. **NO** reescribirla, cambiar su diseño, duplicar componentes, sobrescribir archivos que Claude esté tocando ni ejecutar nuevamente su prompt. Si se necesita modificarla, registrar una dependencia y coordinar el cambio por separado.

### Qué incluye

1. Arquitectura *backend-driven*: catálogo de bancos y remitentes, aprobaciones individuales y contratos de API con persistencia en DynamoDB.
2. Sistema de componentes compartidos de UX, ayudas contextuales y seguimiento por usuario.
3. Mejoras de las vistas existentes: Resumen, Transacciones, Cuentas, Facturas, Presupuestos, Reportes, Comercios y Centro de Guías.
4. Diagnóstico de problemas de recepción sin rehacer el asistente de correo.
5. Accesibilidad, responsive, pruebas y observabilidad segura.

### Reglas de ejecución

- Implementar **un flujo vertical completo** por ticket: API y datos cuando aplique → tipos/cliente frontend → vista → pruebas.
- Revisar la implementación real antes de crear tablas, endpoints, componentes o librerías.
- No cambiar reglas financieras mediante transformaciones de interfaz.
- No introducir bancos, direcciones, dominios ni patrones Gmail quemados en React/Vue/TypeScript, constantes, fixtures de producción o archivos de configuración desplegados.
- No bloquear el lanzamiento de mejoras visuales por componentes opcionales que todavía requieran backend; documentar sus dependencias.
- No reutilizar ni ejecutar la guía de correo previamente delegada como parte de este plan.

---

## 1. Diagnóstico confirmado a partir de las pantallas

| Vista | Hallazgo | Experiencia esperada |
|---|---|---|
| Resumen | Indicadores y gráficos útiles, pero no explican cambios ni conducen a la causa. | Tarjetas con ayuda corta, desglose y navegación a movimientos filtrados. |
| Transacciones | Lista clara; detalle con lenguaje financiero y varias acciones separadas. | Acciones rápidas, explicación de «sin asignar», edición accesible y navegación consistente. |
| Cuentas | Patrimonio, activos, deudas y vinculaciones requieren interpretación. | Explicación visual bajo demanda y alta guiada según el tipo de cuenta. |
| Facturas | Introducción extensa y controles basados en iconos ambiguos. | Estados y acciones legibles; explicar el impacto contable cuando se necesite. |
| Presupuestos | Estado vacío poco motivador y explicación larga en cabecera. | Primer presupuesto guiado y acceso directo desde categorías con gasto. |
| Reportes | Gráficos informativos, pero con exploración limitada. | Desglose navegable, fechas comparables y claridad de períodos parciales. |
| Comercios | Texto largo para explicar agrupación y manejo de categorías. | Bandeja de sugerencias revisables y acciones contextuales. |
| Guías | Biblioteca estática de lecturas. | Centro de tareas, ayuda contextual, continuidad y resolución de problemas. |
| Conexión de correo | Existe trabajo de Claude en curso. | **No rediseñarla nuevamente.** Solo acordar contratos y reutilización futura. |

### Principios de diseño visual

Conservar el tema oscuro, navegación lateral, magenta como acción principal, cian informativo y verde para éxito. Usar mejor jerarquía, menos texto permanente, contrastes accesibles, foco visible, interacción móvil y animaciones discretas que respeten `prefers-reduced-motion`.

---

## 2. Decisiones de arquitectura obligatorias

### 2.1. Propiedad de los datos: backend vs. frontend

| Elemento | Dueño de la verdad | Persistencia / implementación recomendada | Responsabilidad frontend |
|---|---|---|---|
| Catálogo de bancos admitidos | Backend | DynamoDB; alta, baja lógica y edición vía servicio autenticado | Consultar y renderizar; **nunca mantener lista hardcodeada** |
| Nombre, alias visible, orden y estado de un banco | Backend | DynamoDB, identificador estable `bank_id` | Mostrar lo devuelto por API |
| Remitentes y dominios admitidos por banco | Backend | DynamoDB; reglas revisadas, versionadas y auditadas | Mostrar nombres y mensajes; no decidir si un remitente es confiable |
| Autorizaciones de remitentes por usuario | Backend | DynamoDB, aisladas por `user_id`; comprobación real en Ingestion | Crear/revocar mediante API y reflejar estado devuelto |
| Dirección de reenvío Finflow | Backend | Mantener mecanismo actual: alias determinista o persistido, según contrato existente | **Obtenerla exclusivamente desde API**; no componerla con el correo o ID del usuario |
| Texto del filtro de Gmail | Backend preferentemente | Endpoint que construya el filtro desde remitentes autorizados y sintaxis validada | Copiar y presentar el texto; no concatenar `OR` localmente |
| Verificación del reenvío, recepción y procesamiento | Backend | Estados/eventos reales del flujo existente | Consultar y mostrar los estados sin inventar éxitos |
| Cuentas, instrumentos, transacciones, saldo y patrimonio | Backend | Dominios actuales / DynamoDB según implementación | Presentación, captura de acciones y navegación |
| Categorías y agrupación de comercios | Backend | Reglas y datos actuales | Revisión y edición sin recalcular finanzas |
| Progreso de guías por usuario | Backend si debe mantenerse entre dispositivos | DynamoDB; guía, versión, paso, estado, marcas de tiempo | Solicitar/actualizar progreso; conservar estado efímero de animaciones localmente |
| Textos cortos, layout, animaciones y componentes UX | Frontend | Código versionado, traducciones y tokens | **Mantener en frontend**, salvo que haya una necesidad real de CMS |
| Feature flags operativas | Backend si necesitan activación sin despliegue | Configuración persistida y servida por API | Mostrar/ocultar funcionalidades según capacidades verificadas |

**Decisión importante:** *backend-driven* no significa trasladar todos los componentes, textos y animaciones a DynamoDB. Lo configurable como negocio se guarda en el backend; el comportamiento visual y las definiciones de componentes permanecen tipados y versionados en frontend. Esto evita convertir la UI en un intérprete complejo de JSON.

### 2.2. Modelo mínimo conceptual para DynamoDB

**No crear una tabla por cada pantalla sin revisar el diseño actual.** Primero inspeccionar el modelo de claves y patrones de acceso del repositorio; después reutilizar tablas/entidades si corresponde. Los siguientes son **agregados conceptuales**, no un esquema obligatorio.

| Agregado | Campos sugeridos | Operaciones necesarias |
|---|---|---|
| `BankCatalog` | `bank_id`, `display_name`, `status`, `sort_order`, `logo_key`, `version`, `updated_at` | Listar activos, consultar, activar/desactivar, actualizar metadata |
| `BankSenderRule` | `rule_id`, `bank_id`, `match_type`, `normalized_value`, `status`, `version`, `updated_at` | Listar por banco; crear, deshabilitar y auditar reglas |
| `UserSenderApproval` | `user_id`, `approval_id`, `rule_id` o patrón propio validado, `status`, `created_at`, `updated_at` | Listar, aprobar, revocar, consultar autorizaciones efectivas |
| `UserGuideProgress` | `user_id`, `guide_id`, `guide_version`, `status`, `current_step`, `updated_at` | Consultar, comenzar, avanzar, completar, omitir y reiniciar |
| `CatalogAudit` | actor, tipo de cambio, entidad, antes/después o diff seguro, fecha | Trazabilidad administrativa y recuperación |

**Estados recomendados para el catálogo:** `active`, `disabled`, `deprecated` (adaptar a nomenclatura actual). Una desactivación no debe borrar movimientos históricos ni romper nombres de bancos en transacciones anteriores. Los IDs son estables y no dependen del nombre o dominio.

**Reglas de DynamoDB:** diseñar particiones para consultas por catálogo/banco/usuario; evitar `Scan` en cada petición; usar escrituras condicionales para evitar actualizaciones perdidas, paginación y lecturas consistentes cuando sean necesarias; evitar que un usuario lea aprobaciones de otro. El backend puede cachear catálogos de lectura frecuente, con invalidación o TTL y políticas de versión.

### 2.3. Catálogo global ≠ aprobaciones del usuario

Son dos dominios distintos:

1. **Catálogo global administrado:** define qué bancos y qué remitentes conocidos están admitidos o reconocidos. Puede evolucionar sin desplegar el frontend.
2. **Aprobaciones por usuario:** define cuáles de esos remitentes —o remitentes personalizados válidos— acepta **ese usuario**. Deben persistir por usuario y ser aplicadas obligatoriamente en Ingestion.

Agregar un banco o un remitente al catálogo **no debe aprobar automáticamente todos sus correos para todos los usuarios**. Quitar una regla no debe convertir una autorización antigua en aceptación irrestricta. Si se desactiva, el sistema debe aplicar una política explícita y auditada; conservar la historia sin aceptar mensajes nuevos por accidente.

Los dominios y direcciones deben validarse por reglas exactas o de dominio con límites correctos; no usar comparaciones ingenuas de subcadenas. Considerar remitentes falsificados y la semántica real de cabeceras reenviadas; la confianza no puede descansar solo en una tarjeta verde en frontend.

### 2.4. Sobre el alias de correo

El overview describe una dirección derivada determinísticamente del `user_id`. **No sustituir silenciosamente esta decisión por un alias aleatorio guardado en DynamoDB.** Si la dirección actual es estable y expuesta mediante backend, eso ya cumple la separación de responsabilidades con frontend. Si el requerimiento operativo exige almacenar o rotar aliases, plantear una migración **explícita y compatible** que no rompa filtros de Gmail existentes. Lo indispensable es que el frontend jamás lo calcule.

### 2.5. Contratos HTTP y tipado del cliente

Antes de añadir endpoints, buscar si ya existe una API equivalente. Si faltan, plantear **contratos orientativos**, sujetos a los módulos y nombres reales del proyecto:

| Capacidad | Contrato conceptual | Consumidor |
|---|---|---|
| Catálogo activo | `GET /catalog/banks` | Selección de bancos y vistas con filtros |
| Remitentes admitidos | `GET /catalog/banks/{bank_id}/senders` | Panel de detalles y aprobaciones |
| Aprobaciones del usuario | `GET /me/sender-approvals` | Selección y estado personal |
| Autorizar / revocar | `POST /me/sender-approvals`, `DELETE /me/sender-approvals/{id}` | Acciones de usuario |
| Configuración de correo | `GET /me/forwarding` | Dirección y estado verificado |
| Filtro listo para copiar | `GET /me/forwarding/filter` | Flujo de Gmail ya delegado |
| Estado de guías | `GET /me/guides`, `PUT /me/guides/{guide_id}` | Ayudas contextuales |
| Administración autenticada | Endpoints de catálogo bajo el esquema admin actual | Operación del producto, no usuario final |

**Estos endpoints son ejemplos, NO afirmaciones de que ya existen.** Los contratos reales se documentarán tras la auditoría. Preferir OpenAPI generado desde FastAPI/Pydantic y tipos del cliente derivados del contrato; no mantener DTO duplicados manualmente en backend y frontend. Usar el cliente HTTP y la librería de cache/queries ya instalada; separar `api/`, `features/`, `shared/ui/`, `shared/hooks/` siguiendo las convenciones reales, sin reorganizaciones masivas.

**No almacenar secretos ni tokens en configuraciones públicas.** Validar permisos en servidor, nunca únicamente con controles ocultos en la UI. Sin catálogos accesibles por API, mostrar error recuperable, **no** una lista fija de bancos como fallback de producción.

### 2.6. Actualización dinámica del frontend

- **Una sola ruta de datos:** vista → hook/query → cliente tipado → endpoint → dominio/backend → DynamoDB.
- Para catálogos: query con revalidación razonable, control de versión/ETag si ya se usa y refetch al regresar a la vista. No requiere WebSockets para un catálogo pequeño.
- Para mutaciones: invalidar/refrescar queries afectadas y mostrar el estado confirmado por backend; evitar marcar «aprobado» hasta recibir éxito.
- Para cambios concurrentes: usar versionado/condiciones cuando se editan registros administrativos.
- Al eliminar/desactivar una entidad: mantener datos históricos y mensajes coherentes, sin perder referencias.
- Estados homogéneos de `loading`, `empty`, `error`, `ready`, `updating`; accesibles.

### 2.7. Qué mantener fuera del backend

La librería de componentes, rutas frontend, iconos, tokens de color, animaciones, definiciones de tours ligados a selectores del DOM y textos de ayuda genéricos son **código frontend versionado**. No se recomienda un generador de interfaces definido por DynamoDB para una aplicación de esta escala. Sí persistir por usuario qué tour vio u omitió.

---

## 3. Backlog actualizado: esfuerzo, dificultad, tamaño y urgencia

**Tamaño:** S = pequeño, M = mediano, L = grande, XL = muy grande.  
**Dificultad:** Baja / Media / Alta.  
**Urgencia:** P0 = base o riesgo importante; P1 = siguiente ola; P2 = evolutiva.  
**Horas:** estimaciones de esfuerzo humano **si hay que implementar el alcance descrito**. Se ajustan tras la auditoría, especialmente si ya existen APIs o colecciones en DynamoDB. **No incluye el onboarding de correo ya delegado.**

| ID | Iniciativa | Descripción / resultado | Dificultad | Tamaño | Horas | Urgencia |
|---|---|---|---|---|---:|---|
| **ARC-00** | Auditoría del proyecto y límites de trabajo | Mapear rutas, dependencias, APIs, DynamoDB y los archivos del onboarding actualmente en trabajo; detectar hardcodes. | Media | M | **6–12** | **P0** |
| **ARC-01** | Contratos API y cliente tipado | Definir fuente de verdad, Pydantic/OpenAPI, cliente tipado, errores y caché; evitar DTOs y reglas duplicadas. | Media | M | **10–20** | **P0** |
| **ARC-02** | Catálogo dinámico de bancos/remitentes | Persistencia DynamoDB, CRUD administrativo controlado, estados, versionado y listado para frontend. | Alta | L | **20–40** | **P0** |
| **ARC-03** | Aprobaciones individuales de remitentes | Aislamiento por usuario, revocación real, aplicación en Ingestion y sincronización con UI. | Alta | L | **16–32** | **P0** |
| **ARC-04** | Filtro de Gmail generado en backend | Endpoint para obtener texto seguro basado en aprobaciones y reglas vigentes; integración **solo contractual** con onboarding delegado. | Media | M | **8–16** | **P0** |
| **ARC-05** | Progreso de guías persistente | DynamoDB por usuario/guía/versión con APIs para continuar, omitir o reiniciar. | Media | M | **8–16** | **P1** |
| **UX-01** | Microcopy y simplificación de pantallas | Reducir cabeceras, aclarar conceptos financieros y mostrar detalles bajo demanda. | Baja | M | **8–16** | **P0** |
| **UX-02** | Kit visual e interacciones | Tooltip, popover, modal, spotlight, empty state, toast y progreso reutilizables sobre estilos existentes. | Media | L | **12–24** | **P0** |
| **UX-03** | Motor ligero de ayudas contextuales | Tours activados por pantalla y estado real; uso de progreso persistente, reanudación y descarte. | Media | L | **12–24** | **P1** |
| **UX-05** | Diagnóstico de movimientos que no llegan | Estados comprobables, rutas de resolución y mensajes útiles sin reescribir el onboarding. | Alta | M | **12–24** | **P1** |
| **UX-06** | Resumen accionable | Métricas con explicaciones y navegación a desglose filtrado; períodos claros. | Media | M | **10–20** | **P1** |
| **UX-07** | Transacciones y detalle | Acciones rápidas, estados entendibles, edición, «sin asignar» y traslados. | Media | M | **12–24** | **P1** |
| **UX-08** | Cuentas e instrumentos | Alta contextual, vinculación de instrumentos y explicación de patrimonio/deuda. | Alta | L | **16–32** | **P1** |
| **UX-09** | Facturas | Estado previsto/pagado, acciones claras y prevención de confusión con gasto registrado. | Media | M | **12–24** | **P1** |
| **UX-10** | Presupuestos | Estado vacío guiado, crear primer límite y acciones desde categorías relevantes. | Media | M | **10–20** | **P1** |
| **UX-11** | Reportes interactivos | Navegar desde visualizaciones, comparar períodos equivalentes y explicar métricas. | Alta | L | **14–28** | **P1** |
| **UX-12** | Comercios y categorías | Revisión guiada, sugerencias de agrupación, menos textos e impactos claros al editar. | Media | M | **10–20** | **P1** |
| **UX-13** | Centro de Guías | Acciones por tarea, continuidad por usuario, soporte contextual y reejecución de ayudas. | Media | M | **10–20** | **P1** |
| **UX-14** | Avisos móviles (si ya existe soporte) | Ayuda breve para habilitar/preferir avisos; no inventar integración de Telegram. | Media | M | **8–16** | **P2** |
| **UX-15** | Analítica UX sin datos sensibles | Errores, abandonos y finalización de tareas sin correos, importes ni contenido bancario. | Baja | S | **6–12** | **P2** |
| **QA-16** | Accesibilidad, móvil y regresión | Validación transversal de responsive, foco, contraste, pruebas de API y finanzas. | Media | L | **16–32** | **P0 transversal** |

**Estimación máxima del alcance nuevo completo: 236–472 horas-persona**, si hubiera que construir todos los elementos. No es una cifra cerrada: **ARC-02/03/04/05 pueden requerir mucho menos o cero implementación si ya existen**. El trabajo del onboarding de correo está explícitamente excluido. Las estimaciones son aditivas solo como referencia y deberán eliminar solapamientos reales tras ARC-00.

---

## 4. Criterios de aceptación por módulo (sin repetir el flujo Gmail)

### ARC-01/02/03/04 — Backend-driven de verdad

- [ ] Ningún banco, remitente, dominio o email se define como lista fija en el código frontend de producción.
- [ ] Agregar un banco activo y sus remitentes por el mecanismo administrativo provoca que aparezcan en la UI al refrescar o revalidar, **sin despliegue frontend**.
- [ ] Desactivar un banco impide nuevas selecciones según política, pero conserva referencias históricas legibles.
- [ ] Una aprobación individual es una operación de backend y solo afecta a su usuario.
- [ ] Un usuario no puede autorizar, consultar ni revocar remitentes de otro usuario.
- [ ] La ingesta aplica autorizaciones en servidor aunque la UI se manipule.
- [ ] Si cambia una regla, el texto del filtro se obtiene actualizado del backend; la UI avisa que quizá haya que actualizar Gmail, sin asumir que lo modificó automáticamente.
- [ ] No se construyen expresiones Gmail incompatibles, dominios demasiado amplios ni permisos implícitos.
- [ ] Endpoints seguros, pruebas de contratos y migración/seed idempotente si se crean nuevas entidades.

### UX-01/02/03 — Base visual y ayudas

- [ ] Cabeceras cortas, una CTA principal por contexto, información adicional solo bajo demanda.
- [ ] Componentes compartidos respetan tokens actuales, teclado, foco, lector de pantalla y móvil.
- [ ] Cada guía admite `not_started`, `in_progress`, `completed`, `dismissed`, versión y reinicio.
- [ ] El progreso de la guía no reemplaza el estado real de una cuenta o integración.
- [ ] No se muestran ayudas que el usuario haya descartado reiteradamente.

### UX-05 — Diagnóstico, no otro onboarding

- [ ] El usuario distingue «no llegaron correos», «remitente no aprobado», «correo recibido sin extraer» y «movimiento sin asignar» **solo si existe evidencia real para diferenciarlos**.
- [ ] Para información no observable se muestra «No podemos confirmar todavía», no una causa inventada.
- [ ] El resultado lleva a una acción concreta y enlaza a la guía de correo existente cuando corresponde.
- [ ] No modifica el diseño/implementación del asistente ya delegado.

### UX-06 — Resumen

- [ ] Tarjetas de patrimonio/ingresos/gastos/deudas con ayudas cortas y enlaces a detalle.
- [ ] Clic en categoría o transacción abre el filtro/detalle real correspondiente.
- [ ] Comparaciones especifican período y no hacen inferencias financieras sin base.

### UX-07 — Transacciones

- [ ] Se identifican origen automático/manual, estado «sin asignar», edición y eliminación con lenguaje claro.
- [ ] Acciones rápidas discretas en escritorio, accesibles en móvil.
- [ ] Se preserva la lógica del traslado entre cuentas: no se presenta como ingreso/gasto nuevo.

### UX-08 — Cuentas

- [ ] Formularios adecuados al tipo: ahorro, corriente, tarjeta, efectivo, inversión, préstamo/hipoteca, según soporta el backend.
- [ ] Se entiende patrimonio = activos − pasivos, sin forzar cuentas para recibir movimientos.
- [ ] Las vinculaciones e historial retroactivo respetan lo que decide el backend; sin emparejar por conjetura.

### UX-09 — Facturas

- [ ] Se diferencian pagos previstos, confirmados y vencidos según reglas reales.
- [ ] Acciones importantes tienen texto o tooltip accesible.
- [ ] No se contabiliza una factura declarada como si ya hubiera salido dinero.
- [ ] Antes de prometer conciliación automática, auditar si hay soporte real y riesgo de doble conteo.

### UX-10 — Presupuestos

- [ ] Estado vacío con CTA, ejemplo claramente etiquetado si usa datos simulados.
- [ ] Crear un presupuesto con categoría y límite requiere pocos pasos.
- [ ] Categorías sugeridas derivan de movimientos reales; no inventar montos ni bloquear pagos.

### UX-11 — Reportes

- [ ] Gráficos navegan a los movimientos que componen el dato cuando haya filtros compatibles.
- [ ] Períodos parciales y bases de comparación se indican con fechas precisas.
- [ ] Cambios porcentuales tienen explicaciones comprensibles y comprobables.

### UX-12 — Comercios

- [ ] Revisión de sugerencias con confirmación real, respetando decisiones previas.
- [ ] Gestión de categorías con confirmaciones precisas, sin textos extensos permanentes.
- [ ] No se agrupan automáticamente sugerencias inciertas.

### UX-13 — Guías

- [ ] Secciones: «Continuar», «Qué quiero hacer» y «Resolver un problema».
- [ ] Cada tarjeta lleva a acciones existentes, no a nuevas páginas de texto.
- [ ] El usuario puede reabrir una guía ya completada sin alterar estados financieros.
- [ ] La tarjeta de conexión de correo dirige al trabajo ya existente; no hay una segunda implementación.

---

## 5. Plan de ejecución sin colisiones con Claude

| Entrega | Tickets | Qué se implementa | Dependencia / control |
|---|---|---|---|
| **E0 — Auditoría y coordinación** | ARC-00 | Inventario, contratos reales, mapa de archivos «reservados» por la guía de correo y hardcodes. | **Solo análisis**, no tocar código de onboarding. |
| **E1 — Fuente de verdad** | ARC-01, ARC-02, ARC-03, ARC-04 | Catálogo, remitentes, aprobaciones, contrato de filtro, tipos y consumo frontend. | Ejecutar solo las piezas ausentes; validar con el responsable del onboarding. |
| **E2 — Fundamentos UX** | UX-01, UX-02, ARC-05, UX-03 | Microcopy, primitives compartidas y motor de ayudas por usuario. | No reemplazar componentes ya hechos por Claude; reutilizar/adaptar después. |
| **E3 — Tareas cotidianas** | UX-07, UX-08, UX-10 | Transacciones, cuentas y presupuestos. | Flujos verticales independientes; regresión financiera. |
| **E4 — Resto de las vistas** | UX-06, UX-09, UX-11, UX-12 | Resumen, facturas, reportes y comercios. | No prometer funciones sin APIs. |
| **E5 — Ayuda unificada** | UX-05, UX-13 | Diagnóstico, centro de ayuda y enlaces hacia el onboarding **ya existente**. | Integrar al terminar el trabajo paralelo de Claude. |
| **E6 — Evolución** | UX-14, UX-15 | Avisos y telemetría, si corresponden. | Respetar privacidad y soporte backend. |
| **Siempre** | QA-16 | Pruebas, accesibilidad y responsive en cada ticket. | No diferir toda la validación al final. |

**E0 debe producir un archivo `docs/ux/RESERVAS_DE_ARCHIVOS.md`** que identifique qué rutas/componentes/servicios modifica el Claude que desarrolla la guía de correo. Si no se puede confirmar qué archivos modifica, no tocarlos hasta revisar el diff del trabajo en curso. Evitar ramas paralelas que editen los mismos archivos; integrar primero el contrato backend y después adaptar el consumidor con un cambio mínimo acordado.

### Criterio para iniciar una iniciativa

1. Su contrato API y dueño de datos están identificados.
2. No pisa un archivo que esté siendo editado por otro agente.
3. Hay criterios de aceptación y pruebas concretas.
4. Se conoce cuál es la acción observable que mejora al usuario.
5. La mejora puede entregarse y revisarse de forma aislada.

---

## 6. Estrategia específica de frontend recomendada

**Evitar dos extremos:** (a) un frontend con datos financieros y bancos hardcodeados; (b) un frontend completamente generado desde registros DynamoDB. Recomiendo un **frontend delgado y tipado, organizado por funcionalidades**, con un backend que gestione las reglas y estados.

### Estructura conceptual (adaptar al framework actual)

```text
src/
  app/                        # Router, layout, autenticación y providers
  features/
    dashboard/                # Resumen, consultas, gráficos y navegación
    transactions/             # Listas, filtros, detalle y edición
    accounts/                 # Cuentas, instrumentos y vinculaciones
    bills/                    # Facturas
    budgets/                  # Presupuestos
    reports/                  # Reportes
    merchants/                # Comercios y categorías
    guides/                   # Centro de guías, reglas de aparición, progreso
    banking-catalog/          # Catálogo de bancos y aprobaciones desde API
  shared/
    api/                      # Cliente HTTP, tipos generados, errores
    ui/                       # Tooltip, modal, wizard, toasts, empty states
    hooks/                    # Hooks realmente compartidos
    utils/                    # Formato, fechas, moneda: solo presentación
```

**No ejecutar esta reorganización de directorios por defecto.** Es un mapa de responsabilidades, no una orden de refactor masivo. Conservar convenciones actuales si ya funcionan.

### Patrón de consumo de bancos (conceptual)

```typescript
// Ilustración de responsabilidades; NO asumir nombres reales de hooks ni endpoints.
const { data: banks, isLoading, error } = useBankCatalog();
const { data: approvals } = useMySenderApprovals();

// Renderizar información servida por la API.
// Autorizar/revocar solo mediante mutaciones confirmadas por backend.
// Nunca declarar BANKS = ['Bancolombia', 'Lulo', ...] como catálogo de producción.
```

### Ejemplo de respuesta de API (SOLO contrato ilustrativo)

```json
{
  "version": 12,
  "banks": [
    {
      "bank_id": "bank_example",
      "display_name": "Banco de ejemplo",
      "status": "active",
      "logo_url": null,
      "available_sender_count": 2
    }
  ]
}
```

**No exponer todas las reglas de seguridad a una UI pública** si el usuario solo requiere nombre y selección. Separar endpoints públicos/autenticados/administrativos con el menor privilegio posible.

### Dónde guardar el progreso de una guía

Usar DynamoDB como fuente de verdad si se quiere continuidad entre navegador y móvil. El frontend puede mantener estado local efímero del popover, animación o paso actual, pero debe sincronizar eventos durables `completed`, `dismissed` y `current_step` mediante API. Incluir versión de guía para no reciclar un estado antiguo sobre un recorrido nuevo. No persistir detalles de correo, compras o saldos junto con estado de guías.

---

## 7. Prompts para Claude Code

### Prompt A — Auditoría de arquitectura y UX (una sola vez)

```text
ROL: Actúa como Staff Software Engineer / arquitecto de software con experiencia en Python, FastAPI, DynamoDB y frontend moderno, junto con un Product Designer senior.

Lee docs/ux/FINFLOW_PLAN_UX_V2.md y ejecuta EXCLUSIVAMENTE ARC-00. No escribas código de producto todavía.

CONTEXTOS QUE DEBES RESPETAR:
- Ya existe una tarea delegada a Claude para rediseñar el onboarding de asociación de correo. NO ejecutes esa tarea otra vez.
- No edites ni sustituyas sus componentes, instrucciones, estados ni rutas.
- El backend debe ser fuente de verdad de bancos, remitentes, aprobaciones y datos bancarios. DynamoDB se usará según el modelo actual del repositorio.
- El frontend debe renderizar estados y contratos reales; no contener catálogos quemados.
- El frontend puede mantener componentes, animaciones y copys genéricos en código; no construir un CMS dinámico innecesario.

AUDITA DE FORMA SELECTIVA:
1. Ubica rutas y componentes de Resumen, Transacciones, Cuentas, Facturas, Presupuestos, Reportes, Comercios y Guías.
2. Identifica stack frontend, componentes compartidos, estado global, caché de datos, cliente HTTP y tests.
3. Ubica backend de bancos, aprobaciones de remitentes, identidad, forwarding, ingest y endpoints relacionados.
4. Ubica tablas/entidades DynamoDB, access patterns y migraciones o seeds existentes.
5. Localiza TODOS los valores de banco/correo/dominio/Gmail hardcodeados en frontend y dónde se originan.
6. Distingue configuraciones globales, autorizaciones por usuario y metadatos visuales.
7. Confirma si el alias de Finflow se deriva del user_id; no cambies esa lógica sin una justificación y migración.
8. Identifica qué archivos están comprometidos por el trabajo paralelo del onboarding de correo; no los modifiques.
9. Verifica contratos ya disponibles antes de sugerir endpoints o modelos Dynamo nuevos.
10. Marca expresamente lo existente, lo faltante y lo que no has podido confirmar.

ENTREGABLES (crear solo documentación):
- docs/ux/AUDITORIA_UX_TECNICA.md
- docs/ux/MATRIZ_FUENTE_VERDAD.md: dato, dueño, persistencia, API, consumidores y hardcodes.
- docs/ux/CONTRATOS_Y_BRECHAS.md: endpoints existentes vs. por crear, permisos, estados y pruebas.
- docs/ux/RESERVAS_DE_ARCHIVOS.md: archivos del onboarding ya delegado que NO deben tocarse.
- docs/ux/BACKLOG_AJUSTADO.md: esfuerzo real revisado, duplicidades eliminadas y orden de tickets.

EFICIENCIA:
- Empieza por índices de rutas, imports, contratos OpenAPI, repositorios e infraestructura.
- No leas toda la documentación ni todos los archivos por defecto.
- No vuelvas a cargar documentos inspeccionados cuando el resumen existente baste.
- No edites código, no cambies reglas financieras y no hagas commits.

Al terminar, entrega una síntesis de decisiones y recomienda cuál de ARC-01, ARC-02, ARC-03 o ARC-04 falta realmente y debe ejecutarse primero.
```

### Prompt B — Implementar backend-driven de bancos y remitentes (si ARC-00 detecta brecha)

```text
ROL: Staff Backend Engineer (FastAPI/Pydantic/DynamoDB) y responsable de contratos frontend.

TAREA: implementar ÚNICAMENTE el ticket ARC-XX que indique el usuario dentro del plan docs/ux/FINFLOW_PLAN_UX_V2.md. No ejecutar más de un ticket en esta sesión.

LEE SOLO:
- La sección relevante del plan.
- docs/ux/MATRIZ_FUENTE_VERDAD.md y docs/ux/CONTRATOS_Y_BRECHAS.md.
- Los archivos de código y tests directamente relacionados con ARC-XX.

REQUISITOS INNEGOCIABLES:
1. Backend es fuente de verdad. Catálogos globales y autorizaciones por usuario se persisten/controlan por backend, preferentemente reutilizando DynamoDB existente.
2. No introducir arrays de bancos/correos/dominos fijos en frontend de producción.
3. Separar catálogo global del consentimiento por usuario; agregar un banco no aprueba sus remitentes a todos.
4. Respetar aislación por usuario y autorización de endpoints; backend debe validar remitentes durante la ingesta.
5. Preservar referencias históricas si un banco o regla se desactiva; preferir baja lógica.
6. Validar patrones de email/dominio cuidadosamente; no ampliar permisos por error.
7. Reutilizar las estructuras de DynamoDB, Pydantic y APIs ya presentes antes de añadir otras.
8. Si se requiere migración de catálogos anteriores, que sea idempotente y tenga plan de rollback/compatibilidad.
9. Preferir contrato OpenAPI y cliente tipado generado o integrado al stack actual.
10. NO cambiar el alias determinista existente ni archivos del onboarding de correo delegado.
11. Si el frontend requiere cambiar su consumo, documentar la adaptación y reservarla para la integración coordinada.
12. No exponer datos sensibles en logs, mocks ni observabilidad.

PRUEBAS MÍNIMAS:
- Alta, listado, edición y desactivación de banco/regla.
- Usuario A jamás ve ni modifica aprobaciones del usuario B.
- Remitentes desactivados no quedan aceptados accidentalmente.
- Cambios de catálogo no exigen despliegue frontend.
- Manejo de conflictos, validación de patrones, errores de API y compatibilidad histórica.

ENTREGA: contrato real, archivos modificados, diseño de claves/consultas de DynamoDB, casos probados, riesgos, dependencias con onboarding y límites del ticket.
```

### Prompt C — Implementar una mejora UX sin duplicar onboarding (repetir por ticket)

```text
ROL: Product Designer senior, UX Engineer y Frontend Engineer senior trabajando en el repositorio real de Finflow.

TICKET: UX-XX (elegir uno de UX-01/02/03/05/06/07/08/09/10/11/12/13/14/15).

Lee exclusivamente la sección pertinente de docs/ux/FINFLOW_PLAN_UX_V2.md, la auditoría ya existente y los archivos del ticket.

REGLAS:
- La guía de asociación de correo YA ESTÁ DELEGADA a otro Claude: NO volver a implementarla, rediseñarla o tocar sus archivos.
- Datos y configuraciones de negocio proceden del backend. No quemes bancos, dominios, correos, saldos ni estados en frontend.
- Usa el cliente API, tipos, tokens visuales y componentes compartidos existentes.
- La lógica del frontend se limita a presentación, navegación y estados efímeros; validaciones y resultados de negocio pertenecen al backend.
- La guía debe enseñar realizando acciones, no reemplazar un texto largo por otro.
- Conserva el tema oscuro y los acentos magenta, cian, violeta y verde. No introduzcas librerías sin necesidad.
- No alterar cálculos financieros, reglas de cuenta, traslado, facturas o categorización para arreglar la UI.
- Si el ticket necesita una API que no existe, especifica contrato y dependencia. No simules una operación real en producción.
- Entrega un cambio pequeño, integrado y testeado antes de pasar a otra vista.

PLAN DE TRABAJO:
1. Explica brevemente la fricción UX actual y la acción del usuario que debe mejorar.
2. Verifica APIs/estados; identifica componentes reutilizables.
3. Define el diseño, microcopy, estados (vacío/carga/error/éxito), responsive y accesibilidad.
4. Implementa únicamente ese ticket, incluidas las pruebas pertinentes.
5. Valida la navegación, resultado funcional y ausencia de regresiones.
6. Actualiza docs/ux/ESTADO_TICKETS.md con evidencia, archivos y pruebas.

NO HAGAS: refactor global, hardcodes de catálogo, demos falsos de movimientos, reescritura del correo, commits automáticos.

AL FINAL RESPONDE:
- Qué cambió y por qué mejora el flujo.
- Archivos modificados.
- Contratos backend consumidos.
- Casos/estados probados y comandos de pruebas ejecutados.
- Criterios cumplidos, problemas abiertos y próxima tarea sugerida.
```

### Prompt D — Integración y control de calidad entre entregas

```text
ROL: Arquitecto / QA Lead de Finflow.

OBJETIVO: revisar la entrega E-N del plan docs/ux/FINFLOW_PLAN_UX_V2.md, sin iniciar funcionalidades nuevas.

VALIDA:
1. El onboarding de Gmail ya delegado se mantiene funcional e independiente, sin duplicar pasos ni rutas.
2. Catálogo de bancos y remitentes solo desde backend; persistencia DynamoDB y APIs verdaderas.
3. Separación entre catálogo global y autorizaciones por usuario, con pruebas de aislamiento y seguridad.
4. Los alias son de origen backend y el filtro Gmail no se arma mediante listas frontend.
5. El frontend conserva estados reales, refresca consultas tras mutaciones y maneja loading/empty/error.
6. El progreso de guía no implica comprobación de banco, alerta o transacción.
7. Saldos, traslados, cuentas sin asignar, correcciones, facturas y presupuestos mantienen integridad.
8. Móvil, teclado, contraste, accesibilidad, reduced motion y navegación.
9. No hay hardcodes nuevos de bancos/correos ni secretos en frontend o logs.
10. Todas las pruebas declaradas fueron realmente ejecutadas. Si alguna no pudo ejecutarse, registrarlo.

CLASIFICA: BLOQUEANTE / IMPORTANTE / MEJORA.
Corrige únicamente defectos dentro del alcance ya entregado. No refactorices áreas ajenas. Devuelve veredicto APROBADA o NO APROBADA con evidencia y listado de archivos afectos.
```

---

## 8. Definición de terminado (DoD)

- [ ] No se ha duplicado ni sobrescrito la guía de correo actualmente delegada.
- [ ] El dato de negocio tiene fuente de verdad única y contrato identificado.
- [ ] Se descartó cualquier hardcode de bancos, remitentes, dominios y dirección de Finflow en producción.
- [ ] No hay aprobación implícita de remitentes nuevos ni acceso a información de otro usuario.
- [ ] Funciona al crear/desactivar registros de catálogo en backend sin actualizar frontend.
- [ ] Se conserva información histórica y existe estrategia de migración cuando corresponda.
- [ ] La mejora reduce carga cognitiva y ofrece una acción clara.
- [ ] Contempla estado vacío, carga, error, éxito y recuperación cuando sean aplicables.
- [ ] Acepta teclado, lector de pantalla, móvil y preferencia de movimiento reducido.
- [ ] Respeta reglas financieras y no convierte un estado didáctico en éxito bancario.
- [ ] Pruebas ejecutadas y resultados documentados sin afirmaciones inventadas.
- [ ] Existe entrega autónoma y revisable, con documentación mínima y siguiente ticket.

## 9. Orden recomendado para comenzar

1. **Ejecutar Prompt A** — auditar antes de tocar; identificar APIs existentes y reservar archivos de la guía de correo.
2. **Corregir solo brechas ARC-01/02/03/04 realmente ausentes**, comenzando por contrato y modelo de datos.
3. **Crear UX-01 + UX-02** con un primer caso real para validar el kit, sin crear una arquitectura excesiva.
4. **Implementar UX-07 (Transacciones)** y **UX-10 (Presupuestos)**: mejoras visibles y fáciles de validar en uso cotidiano.
5. **Implementar UX-08 (Cuentas)**; después Resumen, Facturas, Reportes y Comercios.
6. **Finalizar UX-03 + ARC-05 + UX-13** cuando las primeras ayudas reales demuestren qué abstracciones hacen falta; no anticipar un sistema de tours demasiado complejo.
7. **Integrar UX-05** y los accesos al onboarding cuando Claude finalice la guía de correo.
8. Ejecutar **QA-16 en cada incremento** y Prompt D antes de cerrar una entrega.

### Resultado esperado

**Finflow obtiene un frontend simple y mantenible, guiado por contratos de backend, y un catálogo bancario administrable desde DynamoDB sin tocar código frontend.** A la vez, mejora cada pantalla y aprende a acompañar al usuario mediante ayudas contextuales, sin repetir el trabajo ya delegado del correo.
