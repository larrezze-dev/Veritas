# Veritas Structural Calculation 3D  — Software Requirements Specification (SRS)

**Versión:** 1.1.0 — **Fecha:** 24 de octubre de 2026  
**Estado:** Decisiones arquitectónicas aprobadas; alcance normativo inicial CTE España  
**Propietario funcional:** Luciano  
**Idioma inicial:** Español  
**Plataforma aprobada:** Windows 11 x64, Python, PySide6, PyVista/VTK y OpenSeesPy

> Este documento constituye la línea base de requisitos funcionales, técnicos y de validación para el desarrollo por Codex de Structural3D. La aplicación es experimental y no está certificada para la justificación de estructuras reales.

## 1. Propósito y convenciones

SVeritas Structural Calculation 3D permitirá modelar estructuras de nudos y barras espaciales, consultar y crear perfiles, aplicar acciones y combinaciones, realizar análisis FEM, visualizar resultados, ejecutar comprobaciones normativas soportadas y producir informes trazables. Se inspira funcionalmente en CYPE 3D para modelado y RSTAB para presentación de resultados, sin copiar su interfaz ni recursos.

**DEBE** significa requisito obligatorio. **P0** define una función del primer recorrido vertical; **P1** define alcance previsto de la versión experimental integral; **P2** define extensión futura. El cierre de un requisito exige prueba documentada.

### 1.1 Invariantes

- Cada barra DEBE tener exactamente un material y una sección asignados. La estructura puede contener barras de acero, aluminio y madera simultáneamente.
- Los perfiles describen geometría y propiedades geométricas; los materiales se almacenan por separado.
- El núcleo de dominio no dependerá directamente de OpenSeesPy, PySide6 ni PyVista.
- Toda propiedad, carga y resultado debe tener unidad, convención y origen identificables.
- Ningún error de cálculo o comprobación no implementada podrá presentarse como resultado satisfactorio.
- Las revisiones antiguas de proyectos, perfiles, resultados y normativas no serán modificadas silenciosamente.

### 1.2 Dentro del alcance

Nudos, barras de pórtico/viga/cercha, apoyos, uniones analíticas rígidas por defecto y configurables por extremo, articulaciones y resortes equivalentes; perfiles normalizados y personalizados; cargas puntuales/distribuidas, peso propio, paneles de reparto, viento, nieve e imperfecciones; casos y combinaciones ELU/ELS; análisis lineal y de segundo orden, modal por fases; resultados y filtros; **CTE España como primera normativa, Eurocódigos y otras normas como plugins posteriores**; informes HTML/PDF.

### 1.3 Fuera del alcance inicial

Detalles constructivos de conexiones, soldaduras, tornillos, placas base, cimentaciones, elementos shell/sólidos resistentes, barras con materiales compuestos, dinámica transitoria avanzada, incendio/fatiga y certificación para uso profesional. **La representación analítica de conexiones rígidas, liberadas o semirrígidas sí está dentro del alcance obligatorio.**

## 2. Actores y casos de uso

| Actor | Función |
|---|---|
| Modelador | Edita geometría, perfiles, materiales, apoyos y cargas |
| Analista | Configura análisis, inspecciona esfuerzos y deformadas |
| Revisor | Examina hipótesis, verificaciones, advertencias e informes |
| Administrador de catálogos | Importa y valida bibliotecas de perfiles |
| Desarrollador Codex | Implementa módulos con pruebas y documentación |

| ID | Flujo de aceptación de extremo a extremo |
|---|---|
| UC-01 | Nuevo proyecto → nudos/barras → perfiles/material → apoyos/cargas → cálculo → resultados → PDF |
| UC-02 | Importar catálogo → filtrar → comparar perfiles → asignar uno a varias barras |
| UC-03 | Dibujar perfil con hueco → validar → propiedades → guardar → asignar |
| UC-04 | Crear panel de cubierta → nieve → repartir sobre barras → verificar equilibrio |
| UC-05 | Generar combinaciones → envolventes → barra y combinación gobernante |
| UC-06 | Modificar sección tras calcular → marcar resultados obsoletos → recalcular |

## 3. Requisitos del modelador y geometría

| ID | Pri. | Requisito y aceptación |
|---|---|---|
| GEO-001 | P0 | Crear nudos con ID estable y coordenadas XYZ en SI. |
| GEO-002 | P0 | Crear barras entre dos nudos distintos; rechazar longitudes degeneradas. |
| GEO-003 | P0 | Asignar exactamente una revisión de material y de sección a cada barra. |
| GEO-004 | P0 | Calcular ejes locales ortonormales y permitir orientar una barra alrededor de su eje longitudinal. |
| GEO-005 | P1 | Selección simple, múltiple, por ventana y por atributos. |
| GEO-006 | P1 | Crear, dividir, copiar, trasladar, rotar, reflejar y replicar elementos. |
| GEO-007 | P1 | Captura geométrica, rejilla 3D, niveles, planos, cotas, etiquetas y capas. |
| GEO-008 | P1 | Edición gráfica y tabular sincronizada sobre el mismo dominio. |
| GEO-009 | P1 | Deshacer/rehacer operaciones atómicas y mantener integridad del modelo. |
| GEO-010 | P1 | Apoyos por GDL, articulaciones/liberaciones, resortes y excentricidades soportadas. |
| GEO-011 | P1 | Detectar barras duplicadas, nudos coincidentes y posibles mecanismos. |
| GEO-012 | P1 | Filtrar, ocultar, aislar y colorear barras por grupo, material y sección. |
| GEO-013 | P2 | Intercambio geométrico DXF con pérdidas y convenciones declaradas. |

**Aceptación GEO:** crear y editar por interfaz un pórtico espacial, cambiar propiedades por selección múltiple, deshacer y volver a abrir el proyecto sin diferencias de entidades, conectividad ni ejes locales.

### 3.1 Uniones analíticas y condiciones de extremo (alineación con propuesta v1.1.0)

Una barra nueva DEBE crearse con sus extremos **I y J rígidos** respecto a sus nudos. Los apoyos del nudo son entidades independientes. El usuario puede modificar cada extremo sin alterar otras barras incidentes en el mismo nudo. «Rígida» supone continuidad cinemática de los GDL en el modelo analítico, no empotramiento del nudo al terreno ni comprobación constructiva de la conexión.

| ID | Pri. | Requisito |
|---|---|---|
| CON-001 | P0 | Rigidez predeterminada de los dos extremos I/J de cada barra nueva. |
| CON-002 | P0 | Configuraciones de los extremos I y J independientes y persistentes. |
| CON-003 | P1 | GDL locales Ux, Uy, Uz, Rx, Ry, Rz con estados rígido, liberado o resorte, cuando sean compatibles con el tipo de elemento. |
| CON-004 | P1 | Rigidez traslacional N/m y rotacional Nm/rad, con conversión de unidades y validación. |
| CON-005 | P1 | Presets rígido, articulado, semirrígido y personalizado, con definición precisa por GDL. |
| CON-006 | P1 | Offsets/excentricidades I/J y referencias de ejes consistentes. |
| CON-007 | P1 | Presets de unión reutilizables y versionados. |
| CON-008 | P1 | Seleccionar el extremo en vista 3D y editarlo en inspector, tabla o asignación múltiple. |
| CON-009 | P1 | Detectar mecanismos, grados de libertad huérfanos y combinaciones incompatibles. |
| CON-010 | P1 | Incluir parámetros de extremos en informes y tablas. |
| CON-011 | P1 | Traducir configuraciones soportadas a OpenSeesPy; rechazar explícitamente las no soportadas. |
| CON-012 | P1 | Historial, invalidación de resultados y pruebas de regresión para uniones. |

**Criterios de aceptación CON:** barra nueva rígida I/J; liberación de un extremo que no modifica el otro; resorte rotacional con respuesta intermedia; mecanismo por liberaciones diagnosticado; guardar y reabrir preservando seis estados por extremo. No se presumirá soporte universal de liberaciones en todo elemento OpenSeesPy: se usarán formulaciones verificadas, nodos auxiliares y resortes cuando proceda.

## 4. Requisitos de materiales

| ID | Pri. | Requisito |
|---|---|---|
| MAT-001 | P0 | Acero, aluminio y madera; parámetros E, G o Poisson según formulación, densidad y propiedades de resistencia diferenciadas. |
| MAT-002 | P0 | ID, nombre, familia, fuente, edición normativa, unidades y versión. |
| MAT-003 | P1 | Catálogo normalizado y materiales personalizados; clonar registros protegidos antes de editar. |
| MAT-004 | P1 | Validaciones dimensionales, propiedades físicamente admisibles y avisos de datos incompletos. |
| MAT-005 | P1 | Reasignar material invalida análisis dependientes; no borra históricos. |
| MAT-006 | P2 | Importación/exportación de datos de materiales con trazabilidad. |

## 5. Biblioteca extensiva de perfiles

### 5.1 Familias

| Material | Familias objetivo |
|---|---|
| Acero | I/H (IPE, HE donde existan datos autorizados), U/C, L, T, Z, SHS, RHS, CHS, barras macizas rectangulares y circulares; perfiles soldados/conformados en extensiones |
| Aluminio | I, U, L, T, SHS, RHS, CHS, macizos y extrusiones de fabricante |
| Madera | Rectangular, cuadrado, circular, maciza y laminada homogénea; otros productos según definición material y norma |

### 5.2 Catálogo

| ID | Pri. | Requisito |
|---|---|---|
| SEC-001 | P0 | Sección con ID y revisión inmutables, familia y parámetros geométricos. |
| SEC-002 | P0 | Separar propiedades geométricas del material; masa lineal derivada de densidad y área. |
| SEC-003 | P0 | Área, centroide, Iy, Iz e Iyz, sistemas de referencia y unidades explícitas. |
| SEC-004 | P1 | Radios de giro, módulos resistentes, J, Cw y otras propiedades cuando exista método validado. |
| SEC-005 | P1 | Buscar por serie, nombre, dimensiones, A, inercias, masa, fabricante y material compatible. |
| SEC-006 | P1 | Ficha con croquis acotado y comparación de perfiles. |
| SEC-007 | P1 | Catálogos de solo lectura, clones editables, favoritos y perfiles recientes. |
| SEC-008 | P1 | Importar CSV/JSON con asignación de columnas/unidades, vista previa y validación por fila. |
| SEC-009 | P1 | Exportar perfiles del usuario y mantener origen, licencia de datos, norma, revisión y método de cálculo. |
| SEC-010 | P1 | Detectar duplicados e incoherencias entre datos publicados y propiedades calculadas. |
| SEC-011 | P1 | Cambiar geometría genera nueva revisión; proyectos existentes conservan referencia previa. |
| SEC-012 | P1 | Sustitución masiva de perfiles en barras con validación de compatibilidad. |
| SEC-013 | P1 | Separar sección apta para FEM de sección cubierta por una norma de diseño. |
| SEC-014 | P2 | Catálogos adicionales de proveedores mediante adaptadores importables. |

Cada valor de propiedad tendrá valor, unidad, origen, método y estado: NO_DISPONIBLE, IMPORTADO_SIN_VERIFICAR, CALCULADO, CONTRASTADO o VERIFICADO. No se inferirá una constante torsional J a partir de la suma Iy+Iz; el alabeo y las propiedades plásticas/efectivas requieren algoritmos específicos.

**Aceptación SEC:** importar 100 filas de prueba, identificar registros incorrectos sin corromper el catálogo, filtrar, comparar, seleccionar, asignar a barras, cerrar y reabrir sin alterar valores ni fuentes.

## 6. Editor y creador de perfiles

| ID | Pri. | Requisito |
|---|---|---|
| EDT-001 | P0 | Editor paramétrico de rectángulo, círculo, anillo, I, U, L, RHS, SHS y CHS. |
| EDT-002 | P1 | Editor 2D con líneas, arcos, polígonos cerrados, huecos, cotas y snapping. |
| EDT-003 | P1 | Operaciones geométricas unión/sustracción de regiones homogéneas. |
| EDT-004 | P0 | Rechazar autointersecciones, contornos abiertos, espesores negativos y huecos inválidos. |
| EDT-005 | P0 | Calcular área, centroide, inercias Iy/Iz/Iyz y ejes principales. |
| EDT-006 | P1 | Dibujar centroide, ejes, contornos, cotas y tabla de propiedades. |
| EDT-007 | P1 | Tratar arcos analíticamente o con discretización de error controlado. |
| EDT-008 | P1 | Calcular J/módulos plásticos solo con algoritmo específico y contrastado, indicando no soportado cuando proceda. |
| EDT-009 | P2 | Cw, propiedades efectivas y secciones abiertas delgadas mediante métodos especializados verificados. |
| EDT-010 | P1 | Guardar nuevo perfil, clon o revisión, con historial y bloqueo de revisiones en uso. |
| EDT-011 | P1 | Comparar propiedades calculadas con valores de catálogo mostrando diferencia relativa. |
| EDT-012 | P1 | Permitir borradores, pero impedir asignar perfiles con geometría inválida. |
| EDT-013 | P1 | Exportar dibujo SVG y definición JSON del perfil. |

**Pruebas EDT:** rectángulo, anillo, I simétrica, L asimétrica y polígono con hueco. Para rectángulo de ancho b y alto h: A=bh; Ix=bh³/12, Iy=hb³/12, previa definición inequívoca de ejes. Para anillo: A=π(R²−r²), I=π(R⁴−r⁴)/4. Contrastar contra soluciones independientes con tolerancias por prueba.

## 7. Cargas, paneles, viento, nieve e imperfecciones

| ID | Pri. | Requisito |
|---|---|---|
| LD-001 | P0 | Casos de carga identificados por ID, categoría y descripción. |
| LD-002 | P0 | Fuerzas/momentos nodales y cargas puntuales/distribuidas de barras según tipos soportados. |
| LD-003 | P0 | Peso propio desde geometría, área, densidad y gravedad, sin duplicidad. |
| LD-004 | P1 | Paneles de contorno geométrico sin rigidez FEM, con barras receptoras configurables. |
| LD-005 | P1 | Reparto tributario o direccional con método explícito y verificación ΣF/ΣM. |
| LD-006 | P1 | Visualizar panel, áreas tributarias, flechas y cargas equivalentes transferidas. |
| LD-007 | P1 | Asistentes viento/nieve con parámetros geográficos/geométricos y normativa/anexo versionados. |
| LD-008 | P1 | Imperfecciones globales y locales mediante geometría o acciones equivalentes, sin duplicación de efectos. |
| LD-009 | P1 | Acciones térmicas cuando el elemento y la formulación FEM estén validados. |
| LD-010 | P1 | Mantener historial de parámetros generadores, sobreescrituras y resultados. |

**Aceptación LD:** para un panel con presión uniforme, las cargas equivalentes deberán satisfacer equilibrio global de fuerzas y momentos en una configuración de referencia dentro de una tolerancia documentada.

## 8. Casos, combinaciones y envolventes

| ID | Pri. | Requisito |
|---|---|---|
| COM-001 | P0 | Combinaciones explícitas como suma de casos y factores definidos. |
| COM-002 | P1 | Combinaciones automáticas ELU/ELS por plugin normativo y anexo nacional. |
| COM-003 | P1 | Acciones incompatibles, simultaneidad, principal y coeficientes correspondientes. |
| COM-004 | P1 | Exponer fórmula expandida y edición normativa de cada combinación. |
| COM-005 | P1 | Superponer solo resultados de análisis matemáticamente lineales compatibles. |
| COM-006 | P1 | En análisis no lineal resolver combinaciones con ejecución propia cuando corresponda. |
| COM-007 | P1 | Envolventes máximas/mínimas con barra, estación y combinación gobernantes. |

## 9. Análisis mediante OpenSeesPy

| ID | Pri. | Requisito |
|---|---|---|
| FEM-001 | P0 | Adaptador determinista de dominio a OpenSeesPy, sin dependencia en el núcleo. |
| FEM-002 | P0 | Barras espaciales con seis GDL por nudo para marcos, y formulaciones de cercha compatibles. |
| FEM-003 | P0 | Definir restricciones, elementos, transformaciones, cargas, solución y tolerancias. |
| FEM-004 | P0 | Recuperar desplazamientos, giros, reacciones y esfuerzos extremos con signos/unidades declarados. |
| FEM-005 | P0 | Iniciar cada corrida con estado FEM limpio y resultados ligados a revisión exacta. |
| FEM-006 | P1 | Segundo orden P-Delta con registro de convergencia. |
| FEM-007 | P1 | Análisis modal básico: frecuencias, periodos, modos. |
| FEM-008 | P1 | Detectar singularidades, mecanismos, ausencia de convergencia y fallos, evitando resultados aparentemente válidos. |
| FEM-009 | P1 | Ejecución aislada y cancelable sin corromper proyecto. |
| FEM-010 | P1 | Registrar versión, parámetros, avisos, duración y estado. |
| FEM-011 | P2 | Estabilidad/no linealidad avanzada con matriz explícita de soporte y validación. |

El pandeo global y lateral-torsional no se consideran resueltos automáticamente por el cálculo estático; requieren parámetros de arriostramiento, modos adecuados y verificaciones normativas.

## 10. Visor de resultados

| ID | Pri. | Requisito |
|---|---|---|
| RES-001 | P0 | Tablas de desplazamientos, giros, reacciones y esfuerzos con unidades. |
| RES-002 | P1 | Diagramas N, Vy, Vz, torsión, My y Mz por estaciones. |
| RES-003 | P1 | Modelo deformado/indeformado y escala de deformación independiente. |
| RES-004 | P1 | Filtros por caso, combinación, material, sección, familia, grupo, nivel y rango. |
| RES-005 | P1 | Mapas de color por magnitud o aprovechamiento y envolventes máximas/mínimas. |
| RES-006 | P1 | Selección sincronizada entre viewport 3D y tablas. |
| RES-007 | P1 | Mostrar signo, sistema de ejes, estación y combinación gobernante. |
| RES-008 | P1 | Guardar vistas/cámaras y exportarlas a informes. |
| RES-009 | P1 | Señalar resultados obsoletos tras edición del modelo. |

## 11. Motor normativo independiente — prioridad CTE España

**DEC-002 aprobada:** la primera implementación normativa será **España / Código Técnico de la Edificación (CTE), Seguridad Estructural**. La familia EN/Eurocódigos, sus anexos nacionales y otros sistemas normativos se incorporarán posteriormente sin sustituir ni mezclar las reglas CTE. El motor de análisis FEM sigue siendo independiente de la normativa seleccionada.

| Paquete inicial | Contenido | Alcance en Structural3D |
|---|---|---|
| CTE DB-SE | Bases de seguridad estructural, verificaciones y combinaciones | Marco prioritario ELU/ELS y criterios generales |
| CTE DB-SE-AE | Acciones en la edificación | Acciones gravitatorias, viento, nieve y otras acciones dentro de su ámbito |
| CTE DB-SE-A | Acero | Comprobaciones de miembros de acero dentro de su ámbito y métodos implementados |
| CTE DB-SE-M | Madera | Comprobaciones de miembros de madera dentro de su ámbito y métodos implementados |
| Aluminio | **Sin DB-SE específico de aluminio en el catálogo CTE** | Mantener análisis FEM; diseño normativo marcado NO_COMPROBADO hasta definir norma española/europea aplicable y su encaje jurídico |

El CTE no debe presentarse como normativa universal de toda estructura: los documentos específicos delimitan su campo de aplicación, principalmente la edificación; otras tipologías necesitarán selección normativa adicional. La **edición consolidada exacta y disposiciones aplicables** se identificarán y fijarán antes de codificar fórmulas, contrastándolas con el texto legal vigente en BOE y documentos oficiales. No se interpretará la fecha genérica del documento como edición técnica de cada DB.

| ID | Pri. | Requisito |
|---|---|---|
| NOR-001 | P1 | Plugins por código, jurisdicción, edición/consolidación, material, familia y comprobación soportada. |
| NOR-002 | P1 | Primero CTE DB-SE, DB-SE-AE, DB-SE-A y DB-SE-M, con trazabilidad por cláusula y edición. |
| NOR-003 | P1 | Cada verificación mostrará cláusula, fórmula, parámetros, valores intermedios, unidades y ratio. |
| NOR-004 | P1 | Estados APROBADO, INCUMPLIDO, NO_APLICABLE, NO_COMPROBADO y ERROR. |
| NOR-005 | P1 | No aprobar si faltan propiedades de sección, material o parámetros de estabilidad. |
| NOR-006 | P1 | Arriostramientos y longitudes de referencia definidos y trazables. |
| NOR-007 | P1 | Prohibir mezclar resultados/comprobaciones de versiones normativas incompatibles sin identificación. |
| NOR-008 | P1 | Test independiente contra ejemplos publicados para cada cláusula implementada. |
| NOR-009 | P2 | Eurocódigos EN 1990/1991/1993/1995/1999 y anexos nacionales como paquetes posteriores; otros sistemas, incluido SIA, separados. |
| NOR-010 | P1 | Para aluminio bajo perfil CTE, indicar cobertura normativa NO_COMPROBADA mientras no se apruebe referencia de cálculo aplicable. |
| NOR-011 | P1 | Validar ámbito de aplicación del DB seleccionado frente a la tipología estructural declarada. |

La cobertura normativa se implementará progresivamente: bases/acciones y acero primero, madera después; aluminio quedará calculable en FEM pero sin comprobación normativa hasta fijar la referencia aplicable. Los futuros Eurocódigos también registrarán sus ediciones exactas y anexos nacionales. Una sección FEM válida puede permanecer NO_COMPROBADA normativamente.

## 12. Persistencia, informes e intercambio

| ID | Pri. | Requisito |
|---|---|---|
| DAT-001 | P0 | Guardar y abrir archivo de proyecto autocontenido/versionado, con comprobación de integridad. |
| DAT-002 | P1 | Migraciones controladas, copias y restauración ante fallo. |
| DAT-003 | P1 | SQLite de catálogos independiente y snapshot de perfiles usados en cada proyecto. |
| DAT-004 | P1 | Mantener ejecuciones históricas inmutables con hash de versión del modelo. |
| DAT-005 | P1 | CSV/JSON para tablas; DXF sujeto a contrato de intercambio específico. |
| RPT-001 | P1 | Editor de plantillas: capítulos activables, orden, selección y filtros. |
| RPT-002 | P1 | Incluir geometría, materiales, perfiles, cargas, combinaciones, análisis y comprobaciones. |
| RPT-003 | P1 | HTML/PDF con tablas paginadas, diagramas, vistas 3D, unidades y referencias. |
| RPT-004 | P1 | Informar de fallos, avisos, cobertura normativa parcial y obsolescencia de resultados. |
| RPT-005 | P1 | Registrar versión del proyecto, solver, norma, catálogo y fecha del cálculo. |
| RPT-006 | P2 | Plantillas corporativas, comparación de revisiones y formatos adicionales. |

## 13. Requisitos no funcionales

| ID | Atributo | Condición de aceptación |
|---|---|---|
| NFR-001 | Plataforma | Instalación y ejecución verificadas en Windows 11 x64. |
| NFR-002 | Offline | Modelado y cálculo sin Internet tras instalación y carga local de datos. |
| NFR-003 | Robustez | El fallo del solver no destruye el modelo ni genera éxito ficticio. |
| NFR-004 | Interfaz | Fluidez de trabajo medida sobre modelo de referencia de 1.000 barras; umbrales cuantitativos tras benchmark. |
| NFR-005 | Precisión | Doble precisión cuando corresponda; tolerancias y unidades documentadas. |
| NFR-006 | Seguridad | Importaciones validadas; no ejecutar código contenido en un proyecto. |
| NFR-007 | Auditoría | Logs de análisis, errores y versiones; historial de cambios esenciales. |
| NFR-008 | Calidad | Tests unitarios, integración, referencia y regresión; tipado, lint y CI. |
| NFR-009 | Localización | Cadenas externalizadas; español inicial, arquitectura preparada para alemán e inglés. |
| NFR-010 | Accesibilidad | Diálogos/tabla navegables por teclado y escalado DPI. |
| NFR-011 | Recuperación | Autoguardado y recuperación explícita sin sobrescritura oculta. |
| NFR-012 | Licencias | Auditoría de dependencias, catálogos y normas antes de redistribución. |

## 14. Modelo conceptual y contratos

| Entidad | Campos esenciales | Relaciones |
|---|---|---|
| Project | ID, versión, unidades, normativa, fecha | Nudos, barras, cargas, análisis |
| Node | ID, XYZ, sistema, atributos | Extremos de barras, apoyos, cargas |
| Member | ID, nodo I/J, sección, material, ejes, offsets, liberaciones | Node, SectionRevision, MaterialRevision |
| MaterialRevision | ID, revisión, familia, E, densidad, resistencias, fuente | Miembros |
| SectionRevision | ID, revisión, familia, geometría, propiedades y estado | Miembros y catálogo |
| SectionProperty | Magnitud, valor, unidad, origen, método, validación | SectionRevision |
| Support | Nodo, restricciones, resortes | Node |
| LoadPanel | Contorno, orientación, método, barras receptoras | LoadCase, Member |
| LoadCase | ID, tipo, edición, unidades | Cargas |
| LoadCombination | ID, expresión, edición normativa | LoadCase |
| AnalysisRun | ID, hash del modelo, solver, configuración, estado | MemberResult |
| DesignCheck | Norma, cláusula, barra, fórmula, estado, ratio | AnalysisRun |
| ReportTemplate | Capítulos, orden, filtros, estilo | Report |

**Servicios principales:** GeometryService, SectionGeometryService, SectionPropertiesService, CatalogService, LoadGenerationService, CombinationService, FemAdapter, AnalysisRunner, ResultsRepository, DesignCodeRegistry, DesignCheckRunner, ReportService.

**Eventos de invalidación:** cambiar geometría, sección, material, apoyos, cargas o solver debe marcar resultados dependientes como OBSOLETOS, sin borrar el historial.

### 14.1 Límites de capas

- Núcleo: entidades, invariantes, unidades y servicios, sin GUI ni importaciones OpenSeesPy.
- GUI: emite comandos de dominio y dibuja su estado, no realiza cálculos FEM directamente.
- Adaptador FEM: traduce dominio y produce resultados normalizados.
- Normativas: consumen resultados y propiedades, no acceden a widgets.
- Informes: consumen instantáneas de modelo/resultados, sin reejecutar cálculos.
- Persistencia: esquemas versionados y migraciones.

## 15. Estructura de proyecto sugerida

| Directorio | Responsabilidad |
|---|---|
| app/ | Inicio, configuración, inyección de dependencias |
| core/geometry/ | Nudos, barras, transformaciones, ejes |
| core/materials/ | Materiales y revisiones |
| core/sections/ | Geometría/properties de secciones |
| core/supports/ | Apoyos y restricciones del nudo |
| core/connections/ | Uniones analíticas por extremo, liberaciones y resortes |
| core/loads/ | Cargas y combinaciones |
| catalogs/ | SQLite, búsqueda, fuentes, importadores |
| section_editor/ | Croquis, perfiles paramétricos, propiedades |
| fem/ | Adaptador OpenSeesPy, ejecución, resultados |
| design_codes/ | Registro de plugins; paquetes CTE primero y EN posteriores |
| gui/ | Modelador, inspector, tablas y resultados |
| reports/ | Plantillas y renderer HTML/PDF |
| persistence/ | Archivos versionados y migraciones |
| tests/ | Casos unitarios, integrados y de referencia |
| docs/ | SRS, ADR, validación y manual |

## 16. Matriz inicial de pruebas de aceptación

| Test ID | Entrada | Expectativa |
|---|---|---|
| T-GEO-001 | Barra entre nudos coincidentes | Rechazo explicativo |
| T-CON-001 | Crear barra y examinar I/J | Ambos extremos rígidos por defecto |
| T-CON-002 | Liberar giro solo en I | J y otras barras del nudo no cambian |
| T-CON-003 | Resorte rotacional con rigidez finita | Respuesta consistente entre límites liberado/rígido |
| T-CON-004 | Configuración que introduce mecanismo | Diagnóstico de inestabilidad; sin cálculo válido |
| T-CON-005 | Guardar/abrir uniones personalizadas | Coinciden seis estados, rigideces y offsets de I/J |
| T-MAT-001 | Intentar dos materiales en una barra | Invariante impide operación |
| T-SEC-001 | Rectángulo b×h | Área y momentos analíticos correctos |
| T-SEC-002 | Anillo con radios R,r | A=π(R²−r²), I=π(R⁴−r⁴)/4 |
| T-SEC-003 | Polígono con hueco | Área neta y centroide correctos |
| T-CAT-001 | CSV con unidades inválidas | Importación informada y sin corrupción |
| T-FEM-001 | Viga biapoyada, P central | Reacciones P/2, Mmax=PL/4 |
| T-FEM-002 | Voladizo con q | Flecha extremo qL⁴/(8EI) bajo hipótesis adecuadas |
| T-LD-001 | Panel uniforme | Equilibrio ΣF y ΣM |
| T-COM-001 | Dos casos lineales | Superposición correcta |
| T-COM-002 | Caso no lineal | Rechaza superposición inapropiada |
| T-RES-001 | Cambiar perfil tras cálculo | Resultados marcados obsoletos |
| T-NOR-001 | Falta propiedad necesaria | NO_COMPROBADO, nunca APROBADO |
| T-RPT-001 | Informe completo | Unidades, revisiones, método, avisos y datos |

Las tolerancias serán específicas por ensayo y fundamentadas; no se establecerá una tolerancia global que pueda ocultar errores.

## 17. Definition of Done para Codex

Un módulo estará terminado únicamente cuando: (1) implementa los IDs de requisitos; (2) incorpora pruebas positivas/negativas; (3) pasa pytest, lint y tipos; (4) documenta ejes, unidades y signos; (5) incluye migraciones necesarias; (6) mantiene APIs de dominio y no introduce acoplamiento indebido; (7) actualiza manual y matriz de cobertura; (8) conserva resultados de pruebas de regresión; (9) informa claramente de funciones no soportadas.

## 18. Plan de entregas incrementales

| Fase | Entregables | Cierre |
|---|---|---|
| F0 | SRS aprobada, ADR, modelo, unidades, CI, fixtures | Base y contratos verificables |
| F1 | Nudos/barras/material/sección elemental + FEM | Viga/cercha/pórtico de referencia |
| F2 | Biblioteca amplia de perfiles, importación y filtros | Catálogo persistente y trazable |
| F3 | Editor paramétrico/libre + propiedades | Secciones contrastadas y asignables |
| F4 | GUI completa de modelado, undo/redo | Estructura creada sin programar |
| F5 | Cargas, paneles, viento, nieve, imperfecciones | Equilibrio y reglas verificadas |
| F6 | Resultados, diagramas, filtros y envolventes | Vista y tablas consistentes |
| F7 | Plugins CTE: DB-SE/DB-SE-AE, acero DB-SE-A y madera DB-SE-M; aluminio pendiente de norma específica | Cada comprobación con cláusula y caso de referencia; EN en expansión posterior |
| F8 | Informes, exportaciones, instalador y validación | Release experimental integrada |

### Primer backlog para Codex

S3D-001 repositorio/CI; S3D-002 sistema de unidades; S3D-003 Node/Member; S3D-004 materiales y sección rectangular; S3D-005 apoyos; S3D-006 cargas; S3D-007 adaptador OpenSeesPy; S3D-008 resultados; S3D-009 pruebas analíticas; S3D-010 viewport mínimo; S3D-011 persistencia; S3D-012 informe básico.

## 19. Riesgos y mitigaciones

| Riesgo | Gravedad | Medida |
|---|---|---|
| J, Cw o módulos plásticos incorrectos de perfil libre | Alta | Solo métodos verificados; estado NO_DISPONIBLE cuando falte |
| Pandeo/arriostramiento mal definido | Crítica | Parámetros explícitos y validación ingenieril |
| Reparto de panel incorrecto | Alta | Ensayos independientes de equilibrio |
| Resultados mezclados entre revisiones | Alta | Hash del modelo, invalidez automática |
| Datos normativos/catalogales sin licencia | Alta | Procedencia y auditoría antes de distribuir |
| No convergencia | Alta | Error visible, logs y bloqueo de estados APROBADO |
| Crecimiento descontrolado | Media-alta | Fases P0/P1/P2 y revisión de cambios |
| Uso estructural real sin auditoría | Crítica | Etiqueta experimental y revisión profesional obligatoria |

## 20. Registro de decisiones

| ID | Decisión | Estado |
|---|---|---|
| DEC-001 | Escritorio local PySide6 + PyVista | **Aprobada** |
| DEC-002 | **España; CTE Seguridad Estructural primero**. EN y otras normativas en futuras ampliaciones; edición de cada documento se fijará durante implementación | **Aprobada** |
| DEC-003 | Español inicial, preparado para alemán/inglés | **Aprobada** |
| DEC-004 | Series europeas de acero, después aluminio/madera | **Aprobada** |
| DEC-005 | A/centroide/inercias primero; J/Cw por familia validada | **Aprobada** |
| DEC-006 | Licencia del producto a decidir tras auditoría | **Aprobado el procedimiento; licencia concreta pendiente** |
| DEC-007 | CSV/JSON inicialmente, DXF después | **Aprobada** |
| DEC-008 | Hardware objetivo tras benchmarking | **Aprobado el procedimiento; umbrales pendientes** |

## 21. Aceptación global de la versión experimental

Un usuario sin escribir código debe poder crear estructura, asignar perfil normalizado o propio, calcular cargas y combinaciones, ejecutar un análisis soportado, explorar esfuerzos/deformadas, identificar ausencia de cobertura normativa, producir un PDF trazable y reabrir su proyecto sin pérdidas. La validación técnica de cada método y la integridad de los datos siguen siendo condiciones independientes.

**Importante:** nunca utilizar esta herramienta como única fuente de dimensionamiento en una obra real sin revisión y validación independiente por profesionales competentes.

## 22. Referencias

- [OpenSeesPy Documentation](https://openseespydoc.readthedocs.io/)
- [OpenSees — Element loads](https://opensees.github.io/OpenSeesDocumentation/user/manual/model/pattern/PlainPatternloadcommands/eleLoad.html)
- [PyVistaQt](https://github.com/pyvista/pyvistaqt)
- [PyVista — Element picking](https://docs.pyvista.org/api/plotting/_autosummary/pyvista.plotter.enable_element_picking.html)
- [Comisión Europea — Eurocódigos](https://single-market-economy.ec.europa.eu/sectors/construction/other-standardisation-initiatives-construction/eurocodes_en)
- [JRC — Segunda generación](https://eurocodes.jrc.ec.europa.eu/2nd-generation/second-generation-eurocodes-what-new)
- [CTE — Seguridad Estructural (documentos oficiales)](https://www.codigotecnico.org/DocumentosCTE/SeguridadEstructural.html)
- [CTE DB-SE](https://www.codigotecnico.org/pdf/Documentos/SE/DBSE.pdf)
- [CTE DB-SE-AE](https://www.codigotecnico.org/pdf/Documentos/SE/DBSE-AE.pdf)
- [CTE DB-SE-A](https://www.codigotecnico.org/pdf/Documentos/SE/DBSE-A.pdf)

---

**Estado SRS 1.1.0:** decisiones DEC-001 a DEC-008 registradas según aprobación del Product Owner; DEC-002 orientada a CTE España, y CON-001 a CON-012 integradas desde la propuesta v1.1.0. La matriz detallada de cláusulas CTE, ediciones, tolerancias numéricas y fuentes de cada catálogo deberá especificarse y verificarse antes del desarrollo normativo.
