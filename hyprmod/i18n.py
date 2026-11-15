"""Internationalization support — Spanish translations for UI strings.

Provides a ``_()`` function that returns the translation of a UI string
in the currently selected language, falling back to the original English
text when no translation exists.  Call ``set_language()`` to switch
between supported languages at runtime.
"""

# ---------------------------------------------------------------------------
# Supported languages
# ---------------------------------------------------------------------------

_SUPPORTED_LANGUAGES = {
    "en": "English",
    "es": "Español",
}

# ---------------------------------------------------------------------------
# Current language (mutable at runtime)
# ---------------------------------------------------------------------------

_current_language: str = "en"

# ---------------------------------------------------------------------------
# Translation catalog
# ---------------------------------------------------------------------------
# Keys are the original English strings.  Values are their Spanish
# equivalents.  Keep entries sorted alphabetically by key for easier
# maintenance.
# ---------------------------------------------------------------------------

_CATALOG: dict[str, str] = {
    # -- About dialog (ui/about.py) --
    "A native GTK4/libadwaita settings app for Hyprland": (
        "Una aplicación de configuración nativa GTK4/libadwaita para Hyprland"
    ),
    "Hyprland (running): not detected": "Hyprland (ejecutándose): no detectado",
    "Hyprland (running): {version}": "Hyprland (ejecutándose): {version}",
    "Hyprland schema (bundled): {schema_version}": (
        "Esquema de Hyprland (incluido): {schema_version}"
    ),
    # -- Banner (ui/banner.py) --
    "Changes saved to disk": "Cambios guardados en disco",
    "Discard": "Descartar",
    "Save as new profile": "Guardar como nuevo perfil",
    "Save now": "Guardar ahora",
    "Save without updating profile": "Guardar sin actualizar perfil",
    "Unsaved changes — applied live, not saved to disk": (
        "Cambios no guardados — aplicados en vivo, no guardados en disco"
    ),
    # -- Binds dialog (binds/dialog.py) --
    "Action": "Acción",
    "Add Keybind": "Añadir atajo",
    "Apply": "Aplicar",
    "Argument": "Argumento",
    "Back": "Atrás",
    "By amount": "Por cantidad",
    "Cancel": "Cancelar",
    "Command": "Comando",
    "Custom value": "Valor personalizado",
    "Custom…": "Personalizado…",
    "Direction": "Dirección",
    "Down": "Abajo",
    "Edit Keybind": "Editar atajo",
    "Forward": "Adelante",
    "Full": "Completa",
    "Height": "Altura",
    "Left": "Izquierda",
    "Maximize": "Maximizar",
    "Mode": "Modo",
    "Name (optional)": "Nombre (opcional)",
    "Next workspace": "Siguiente escritorio",
    "No": "No",
    "Off": "Desactivado",
    "On": "Activado",
    "Pick a button…": "Selecciona un botón…",
    "Pick from installed apps": "Seleccionar de aplicaciones instaladas",
    "Previous workspace": "Escritorio anterior",
    "Right": "Derecha",
    "Toggle": "Alternar",
    "To exact size": "A tamaño exacto",
    "Up": "Arriba",
    "Width": "Ancho",
    "Workspace": "Escritorio",
    "Yes": "Sí",
    # -- Categories (ui/sidebar.py) --
    "Advanced": "Avanzado",
    "Autostart": "Inicio automático",
    "Display": "Pantalla",
    "Env Variables": "Variables de entorno",
    "Input": "Entrada",
    "Keybinds": "Atajos de teclado",
    "Layer Rules": "Reglas de capa",
    "Layouts": "Distribuciones",
    "Look & Feel": "Apariencia",
    "Monitors": "Monitores",
    "Plugin Settings": "Configuración de plugins",
    "Profiles": "Perfiles",
    "Search options (Ctrl+F)": "Buscar opciones (Ctrl+F)",
    "Search options…": "Buscar opciones…",
    "Settings": "Configuración",
    "Startup": "Inicio",
    "Window Management": "Gestión de ventanas",
    "Window Rules": "Reglas de ventana",
    "Workspaces": "Escritorios",
    # -- Animations page (pages/animations.py) --
    "Bezier Curve Editor": "Editor de curvas Bézier",
    "Border": "Borde",
    "Border Angle": "Ángulo del borde",
    "Close": "Cerrar",
    "Create and manage animation curves": "Crear y gestionar curvas de animación",
    "Dim": "Atenuar",
    "Fade": "Desvanecer",
    "Fade In": "Desvanecer entrada",
    "Fade Out": "Desvanecer salida",
    "Fading": "Desvanecimiento",
    "Global": "Global",
    "Layers": "Capas",
    "Layers In": "Capas entrada",
    "Layers Out": "Capas salida",
    "Monitor Added": "Monitor añadido",
    "Move": "Mover",
    "new override": "nueva anulación",
    "Open": "Abrir",
    "Other": "Otro",
    "Popups": "Ventanas emergentes",
    "Popups In": "Ventanas emergentes entrada",
    "Popups Out": "Ventanas emergentes salida",
    "remove override on save": "eliminar anulación al guardar",
    "Shadow": "Sombra",
    "Special": "Especial",
    "Special In": "Especial entrada",
    "Special Out": "Especial salida",
    "Switch": "Cambiar",
    "Switch In": "Cambio entrada",
    "Switch Out": "Cambio salida",
    "Windows": "Ventanas",
    "Windows & Layers": "Ventanas y capas",
    "Zoom Factor": "Factor de zoom",
    # -- Empty states --
    "No Matches": "Sin coincidencias",
    "No Open Windows": "Sin ventanas abiertas",
    "No Pending Changes": "Sin cambios pendientes",
    "No Results": "Sin resultados",
    "Try a different search term.": "Intenta con otro término de búsqueda.",
    # -- Deprecation banner (ui/deprecation_banner.py) --
    "Deprecated Hyprland syntax detected in 1 file — review the fix?": (
        "Sintaxis obsoleta de Hyprland detectada en 1 archivo — ¿revisar la corrección?"
    ),
    "Deprecated Hyprland syntax detected in {count} files — review the fixes?": (
        "Sintaxis obsoleta de Hyprland detectada en {count} archivos — ¿revisar las correcciones?"
    ),
    "Don't show again": "No mostrar de nuevo",
    "Don't show again for this scan": "No mostrar de nuevo para este análisis",
    "Review…": "Revisar…",
    # -- Lua migration banner (ui/lua_migration_banner.py) --
    "Hyprland {ver} recommends Lua configs — ": "Hyprland {ver} recomienda configuraciones Lua — ",
    "migrate your hyprland.conf with one click": "migrá tu hyprland.conf con un clic",
    "Migrate to Lua…": "Migrar a Lua…",
    # -- Deprecation dialog (ui/deprecation_dialog.py) --
    "after migration": "después de la migración",
    "Apply selected": "Aplicar seleccionados",
    "current": "actual",
    "Detected but not auto-fixable": "Detectados pero no corregibles automáticamente",
    (
        "Each file is rewritten in place; a timestamped backup is saved in a "
        ".hyprmod-backups/ directory beside it."
    ): (
        "Cada archivo se reescribe en su lugar; se guarda una copia de seguridad "
        "con marca de tiempo en un directorio .hyprmod-backups/ junto a él."
    ),
    "Fixable files": "Archivos corregibles",
    "Migrate deprecated syntax": "Migrar sintaxis obsoleta",
    "No deprecations found": "No se encontraron obsoleturas",
    "This path resolves through a symlink": "Esta ruta se resuelve a través de un enlace simbólico",
    "These deprecations need a manual edit — there's no safe automatic rewrite.": (
        "Estas obsoleturas requieren edición manual; no hay una reescritura automática segura."
    ),
    "Your Hyprland config is up to date.": "Tu configuración de Hyprland está actualizada.",
    "Writing it will modify the symlink target, which may live in a dotfiles repository.": (
        "Escribirlo modificará el destino del enlace simbólico, "
        "que puede estar en un repositorio de dotfiles."
    ),
    # -- Pending changes (pages/pending.py) --
    "Added": "Añadido",
    "Animations": "Animaciones",
    "Config diff preview": "Vista previa de diferencias de configuración",
    "Comparison between the saved config and what the next save would write.": (
        "Comparación entre la configuración guardada y lo que se escribirá al guardar."
    ),
    "Cursor": "Cursor",
    "Edits made on any page will appear here for you to review before saving.": (
        "Los cambios realizados en cualquier página aparecerán aquí "
        "para que los revises antes de guardar."
    ),
    "Modified": "Modificado",
    "Options": "Opciones",
    "Removed": "Eliminado",
    "Revert this change": "Deshacer este cambio",
    "unsaved change": "cambio no guardado",
    "unsaved changes": "cambios no guardados",
    "value updated": "valor actualizado",
    # -- Shortcuts overlay (ui/shortcuts.py) --
    "Close search": "Cerrar búsqueda",
    "General": "General",
    "Help": "Ayuda",
    "Redo change": "Rehacer cambio",
    "Save changes": "Guardar cambios",
    "Search": "Búsqueda",
    "Search options": "Buscar opciones",
    "Show keyboard shortcuts": "Mostrar atajos de teclado",
    "Undo last change": "Deshacer último cambio",
    # -- Settings page (pages/settings.py) --
    "Choose the display language for HyprMod.": "Elige el idioma de la interfaz de HyprMod.",
    "Interface language": "Idioma de la interfaz",
    "Language": "Idioma",
    # -- Window banner (window.py) --
    "Hyprland not detected — changes will be saved to config files but not applied live": (
        "Hyprland no detectado: los cambios se guardarán en los archivos de "
        "configuración pero no se aplicarán en vivo"
    ),
    # -- Workspace presets (binds/dialog.py) --
    "First empty": "Primero vacío",
    "Last visited": "Último visitado",
    "Special (scratchpad)": "Especial (bloc de notas)",
    "Workspace 1": "Escritorio 1",
    "Workspace 2": "Escritorio 2",
    "Workspace 3": "Escritorio 3",
    "Workspace 4": "Escritorio 4",
    "Workspace 5": "Escritorio 5",
    "Workspace 6": "Escritorio 6",
    "Workspace 7": "Escritorio 7",
    "Workspace 8": "Escritorio 8",
    "Workspace 9": "Escritorio 9",
    "Workspace 10": "Escritorio 10",
    # -- Additional UI strings (pages, dialogs, banners) --
    "(entry incomplete)": "(entrada incompleta)",
    "(next save)": "(próximo guardado)",
    "(rule incomplete)": "(regla incompleta)",
    "(selector required)": "(selector obligatorio)",
    "1 change": "1 cambio",
    "1 result": "1 resultado",
    "1 unsaved change": "1 cambio no guardado",
    "A profile with this name already exists": "Ya existe un perfil con este nombre",
    "About HyprMod": "Acerca de HyprMod",
    "Activate": "Activar",
    "Active": "Activo",
    "Add a condition": "Añadir una condición",
    "Add an action": "Añadir una acción",
    "Add autostart entry": "Añadir entrada de inicio automático",
    "Add color stop": "Añadir parada de color",
    "Add environment variable": "Añadir variable de entorno",
    "Add keybind": "Añadir atajo",
    "Add layer rule": "Añadir regla de capa",
    "Add layout": "Añadir distribución",
    "Add one or more conditions. Hyprland matches windows where ALL conditions apply.": (
        "Añade una o más condiciones. Hyprland coincide con ventanas donde se"
        " cumplen TODAS las condiciones."
    ),
    "Add option…": "Añadir opción…",
    "Add plugin setting": "Añadir ajuste de plugin",
    "Add selected option": "Añadir opción seleccionada",
    "Add window rule": "Añadir regla de ventana",
    "Add workspace rule": "Añadir regla de escritorio",
    "Angle (degrees)": "Ángulo (grados)",
    (
        "Another connected monitor matches the same description prefix."
        " Hyprland may apply this config to the wrong monitor"
    ): (
        "Otro monitor conectado comparte el mismo prefijo de descripción."
        " Hyprland podría aplicar esta configuración al monitor equivocado"
    ),
    "Another connected monitor shares the same description prefix": (
        "Otro monitor conectado comparte el mismo prefijo de descripción"
    ),
    "Appearance": "Apariencia",
    "Apply curve to animation": "Aplicar curva a la animación",
    "Apply safe defaults": "Aplicar valores seguros",
    "Apply these actions": "Aplicar estas acciones",
    "Apply this rule": "Aplicar esta regla",
    "Apply this rule to HyprMod itself?": "¿Aplicar esta regla a HyprMod?",
    "Auto-save": "Guardado automático",
    "Behavior": "Comportamiento",
    "Behaviour": "Comportamiento",
    "Bind to monitor": "Vincular al monitor",
    "Border size (px)": "Tamaño del borde (px)",
    "Border size override": "Anulación del tamaño del borde",
    "Browse…": "Examinar…",
    "Can't Read Your Config": "No se puede leer tu configuración",
    "Command line": "Línea de comandos",
    "Config file path": "Ruta del archivo de configuración",
    "Configuration": "Configuración",
    "Control Points": "Puntos de control",
    "Cursor size (pixels)": "Tamaño del cursor (píxeles)",
    "Cursor theme": "Tema del cursor",
    "Curve": "Curva",
    "Default": "Predeterminado",
    "Default for monitor": "Predeterminado para el monitor",
    "Default name override": "Anulación del nombre predeterminado",
    (
        "Define custom settings for Hyprland plugins. These settings will"
        " be written into a plugin { ... } block."
    ): (
        "Define ajustes personalizados para plugins de Hyprland. Estos ajustes"
        " se escribirán en un bloque plugin { ... }."
    ),
    "Delete": "Eliminar",
    "Delete curve": "Eliminar curva",
    "Description not reported by this monitor": "El monitor no informa de una descripción",
    "Discard changes": "Descartar cambios",
    "Display name": "Nombre mostrado",
    "Drag control points to adjust the curve": (
        "Arrastra los puntos de control para ajustar la curva"
    ),
    "Drag monitors to reposition": "Arrastra los monitores para recolocarlos",
    "Duplicate": "Duplicar",
    "Each gets its own .lua next to the original .conf.": (
        "Cada uno recibe su propio .lua junto al .conf original."
    ),
    "Edit animation settings": "Editar ajustes de animación",
    "Edit curve": "Editar curva",
    "Enabled": "Activado",
    "Enter a name for your curve.": "Introduce un nombre para tu curva.",
    (
        "Existing .lua files would block the write. Toggle 'Overwrite"
        " existing .lua files' to continue."
    ): (
        "Los archivos .lua existentes bloquearían la escritura. Activa"
        " «Sobrescribir archivos .lua existentes» para continuar."
    ),
    "Files to write": "Archivos a escribir",
    "Filter keybinds…": "Filtrar atajos…",
    "Gaps": "Márgenes",
    "HDR Max Luminance": "Luminancia máxima HDR",
    "HDR Min Luminance": "Luminancia mínima HDR",
    "HyprMod cannot safely edit this keybind": "HyprMod no puede editar este atajo de forma segura",
    (
        "HyprMod gives you a visual interface for all Hyprland settings"
        " with live preview. Every change is applied instantly to your"
        " running compositor."
    ): (
        "HyprMod te ofrece una interfaz visual para todos los ajustes de"
        " Hyprland con vista previa en vivo. Cada cambio se aplica al instante"
        " en tu compositor."
    ),
    (
        "Hyprland exports this variable to processes it spawns (autostart"
        " commands, dispatchers, terminal launches). Changes take effect"
        " on the next Hyprland session."
    ): (
        "Hyprland exporta esta variable a los procesos que inicia (comandos de"
        " inicio automático, lanzadores, terminales). Los cambios surten"
        " efecto en la próxima sesión de Hyprland."
    ),
    "Identify by description": "Identificar por descripción",
    "Inherits from parent. Editing will create an override.": (
        "Hereda del elemento superior. Editarlo creará una anulación."
    ),
    "Keep the workspace alive and run a command when it first opens.": (
        "Mantiene el escritorio activo y ejecuta un comando cuando se abre por primera vez."
    ),
    "Keyboard Shortcuts": "Atajos de teclado",
    "Keyboard layouts": "Distribuciones de teclado",
    "Lifecycle": "Ciclo de vida",
    "Lua migration complete": "Migración a Lua completada",
    "Managed": "Gestionado",
    "Match this layer surface": "Coincidir con esta superficie de capa",
    "Match windows where…": "Coincidir con ventanas donde…",
    "Max Avg Luminance": "Luminancia media máxima",
    "Migrate": "Migrar",
    "Migrate to Lua": "Migrar a Lua",
    "Mirror": "Espejar",
    "Missing Cairo Bindings": "Faltan las bibliotecas Cairo",
    "Monitor": "Monitor",
    "Monitors have gaps between them — cursor won't be able to move across": (
        "Hay huecos entre los monitores: el cursor no podrá cruzarlos"
    ),
    "Name": "Nombre",
    "Namespace regex": "Expresión regular del espacio de nombres",
    (
        "Naming a rule lets you enable / disable it at runtime via"
        " Hyprland's Lua API or hyprctl. Anonymous rules are written as"
        " the compact one-line form."
    ): (
        "Nombrar una regla te permite activarla o desactivarla en tiempo de"
        " ejecución mediante la API Lua de Hyprland o hyprctl. Las reglas"
        " anónimas se escriben en la forma compacta de una línea."
    ),
    "No Autostart Entries": "Sin entradas de inicio automático",
    "No Custom Plugins": "Sin plugins personalizados",
    "No Environment Variables": "Sin variables de entorno",
    "No Keybinds": "Sin atajos de teclado",
    "No Layer Rules": "Sin reglas de capa",
    "No Monitors Detected": "No se detectaron monitores",
    "No Profiles": "Sin perfiles",
    "No Window Rules": "Sin reglas de ventana",
    "No Workspace Rules": "Sin reglas de escritorio",
    "No active profile": "Sin perfil activo",
    (
        "Off: run once at Hyprland startup (recommended).\nOn: re-run every"
        " time the config is reloaded. Useful for cleanup commands like"
        " pkill -SIGUSR1."
    ): (
        "Desactivado: se ejecuta una vez al iniciar Hyprland"
        " (recomendado).\nActivado: se ejecuta cada vez que se recarga la"
        " configuración. Útil para comandos de limpieza como pkill -SIGUSR1."
    ),
    "On first map": "Al abrirse por primera vez",
    "Open a window in Hyprland (or click refresh) to pick from it.": (
        "Abre una ventana en Hyprland (o pulsa actualizar) para elegirla."
    ),
    "Optional. Override the workspace's default display name (shown in status bars and pickers).": (
        "Opcional. Sobrescribe el nombre mostrado por defecto del escritorio"
        " (se usa en barras de estado y selectores)."
    ),
    (
        "Optional. Pin this workspace to a specific output. Disconnected"
        " monitors still apply when they return."
    ): (
        "Opcional. Fija este escritorio a una salida concreta. Los monitores"
        " desconectados se aplicarán cuando vuelvan."
    ),
    "Overridden": "Anulado",
    (
        "Override inner/outer gaps for this workspace. Per-side mode"
        " mirrors CSS: top, right, bottom, left."
    ): (
        "Sobrescribe los márgenes internos y externos de este escritorio. El"
        " modo por lados sigue CSS: arriba, derecha, abajo, izquierda."
    ),
    "Override this keybind": "Anular este atajo",
    "Override this setting": "Anular este ajuste",
    "Override this variable": "Anular esta variable",
    (
        "Override window decoration for this workspace. Settings left on"
        " “Use global” inherit your normal config."
    ): (
        "Sobrescribe la decoración de ventanas de este escritorio. Los ajustes"
        " en «Usar global» heredan tu configuración normal."
    ),
    "Overwrite existing .lua files": "Sobrescribir archivos .lua existentes",
    "Overwrite existing file?": "¿Sobrescribir el archivo existente?",
    "Pending Changes": "Cambios pendientes",
    "Persistent": "Persistente",
    "Pick an Application": "Elegir una aplicación",
    "Pick an Open Window": "Elegir una ventana abierta",
    (
        "Pick an installed app or type any shell command. Hyprland passes"
        " this to /bin/sh -c, so quoting and metacharacters work as in"
        " shell."
    ): (
        "Elige una aplicación instalada o escribe cualquier comando de shell."
        " Hyprland lo pasa a /bin/sh -c, así que las comillas y los"
        " metacaracteres funcionan como en el shell."
    ),
    "Pick from an open window": "Elegir de una ventana abierta",
    "Pick what Hyprland should do for matching layer surfaces.": (
        "Elige qué debe hacer Hyprland con las superficies de capa coincidentes."
    ),
    "Pick what Hyprland should do when a matching window opens.": (
        "Elige qué debe hacer Hyprland cuando se abra una ventana coincidente."
    ),
    "Plugin Name": "Nombre del plugin",
    "Plugin Setting": "Ajuste de plugin",
    "Position": "Posición",
    "Preset": "Preajuste",
    "Preview": "Vista previa",
    "Re-query open windows": "Volver a consultar ventanas abiertas",
    "Re-run on every config reload": "Reejecutar en cada recarga de configuración",
    "Refresh monitors": "Actualizar monitores",
    (
        "Regex matching the layer surface namespace. Examples: ‘waybar’,"
        " ‘^(rofi|wofi)$’, ‘notifications’."
    ): (
        "Expresión regular que coincide con el espacio de nombres de la capa."
        " Ejemplos: «waybar», «^(rofi|wofi)$», «notifications»."
    ),
    "Remove": "Quitar",
    "Remove color stop": "Quitar parada de color",
    "Remove layout": "Quitar distribución",
    "Remove override": "Quitar anulación",
    "Remove this action": "Quitar esta acción",
    "Remove this condition": "Quitar esta condición",
    "Removed (pending save)": "Eliminado (pendiente de guardar)",
    "Rename": "Cambiar nombre",
    "Rename Curve": "Cambiar nombre de la curva",
    "Rename Profile": "Cambiar nombre del perfil",
    "Rename curve": "Cambiar nombre de la curva",
    "Reordered": "Reordenado",
    "Report a bug": "Informar de un error",
    "Resolution": "Resolución",
    "Restore this item": "Restaurar este elemento",
    "Resulting config": "Configuración resultante",
    "Revert": "Revertir",
    "Revert to saved curve": "Volver a la curva guardada",
    "Rule": "Regla",
    "Run this command now": "Ejecutar este comando ahora",
    "SDR Brightness": "Brillo SDR",
    "SDR Max Luminance": "Luminancia máxima SDR",
    "SDR Min Luminance": "Luminancia mínima SDR",
    "SDR Saturation": "Saturación SDR",
    "Safe Defaults": "Valores seguros",
    "Save": "Guardar",
    "Save Current as Profile": "Guardar actual como perfil",
    "Save as Custom Curve": "Guardar como curva personalizada",
    "Save as new custom curve": "Guardar como nueva curva personalizada",
    "Save as…": "Guardar como…",
    "Save current": "Guardar actual",
    "Save current as new profile": "Guardar actual como nuevo perfil",
    "Save your current configuration as a profile to track it": (
        "Guarda tu configuración actual como perfil para hacerle seguimiento"
    ),
    "Saved config is up to date": "La configuración guardada está actualizada",
    "Saved profiles": "Perfiles guardados",
    "Scale": "Escala",
    "Search applications…": "Buscar aplicaciones…",
    "Search by class or title…": "Buscar por clase o título…",
    "Search keybinds": "Buscar atajos",
    "Select config file": "Seleccionar archivo de configuración",
    "Setting Key": "Clave del ajuste",
    "Setting Value": "Valor del ajuste",
    "Shell command — same syntax as ‘exec’ entries. Example: ‘kitty’.": (
        "Comando de shell: misma sintaxis que las entradas «exec». Ejemplo: «kitty»."
    ),
    "Sourced sub-configs": "Subconfiguraciones incluidas",
    "Speed": "Velocidad",
    "Style": "Estilo",
    "Summary": "Resumen",
    "Supported Plugins": "Plugins compatibles",
    "Supports:": "Compatible con:",
    (
        "The Lua API doesn't have a direct equivalent for these. They'll"
        " be left out of the generated .lua and you'll need to port them"
        " by hand."
    ): (
        "La API de Lua no tiene un equivalente directo para esto. Se omitirá"
        " en el .lua generado y tendrás que adaptarlo manualmente."
    ),
    "Theme": "Tema",
    "Theme and size": "Tema y tamaño",
    "This monitor does not report a description": "Este monitor no informa de una descripción",
    "Transform": "Transformación",
    "Type": "Tipo",
    (
        "Type the layer namespace as a regex. Common namespaces: waybar,"
        " notifications, rofi, mako, dunst, wallpaper."
    ): (
        "Escribe el espacio de nombres de la capa como expresión regular."
        " Espacios comunes: waybar, notifications, rofi, mako, dunst,"
        " wallpaper."
    ),
    "Value": "Valor",
    "Variable": "Variable",
    "View pending changes": "Ver cambios pendientes",
    "Welcome to HyprMod": "Bienvenido a HyprMod",
    "Which workspace(s) this rule applies to.": "A qué escritorio(s) se aplica esta regla.",
    "Won't migrate": "No se migrará",
    (
        "Your existing hyprland.conf stays untouched. The wizard writes"
        " hyprland.lua and one .lua per sourced file alongside the"
        " originals."
    ): (
        "Tu hyprland.conf actual no se modifica. El asistente escribe"
        " hyprland.lua y un .lua por cada archivo incluido junto a los"
        " originales."
    ),
    "changes": "cambios",
    "deleted": "eliminado",
    "disabled": "desactivado",
    "in a different order": "en un orden diferente",
    "new": "nuevo",
    "removing override": "eliminando anulación",
    "results": "resultados",
    "set to": "establecido en",
    "will be removed on save": "en espera de eliminación al guardar",
    # -- Hyprland schema strings (data/schema/options.json) --
    "A comma-separated list of preconfigured widths for colresize +conf/-conf": (
        "Lista separada por comas de anchos preconfigurados para colresize +conf/-conf"
    ),
    "Acceleration profile": "Perfil de aceleración",
    "Activation Function": "Función de activación",
    "Active border color": "Color del borde activo",
    (
        "Active layouts in order; the first is the default. Add a"
        " layout-switch shortcut under Keyboard options."
    ): (
        "Distribuciones activas en orden; la primera es la predeterminada."
        " Añade un atajo para cambiar de distribución en las opciones de"
        " teclado."
    ),
    "Active opacity": "Opacidad activa",
    "Adaptive": "Adaptativo",
    "After active": "Después del activo",
    "Allow running applications using X11": "Permitir ejecutar aplicaciones con X11",
    "Allow screen tearing for reduced latency": (
        "Permitir tearing de pantalla para reducir la latencia"
    ),
    "Allow small split": "Permitir división pequeña",
    "Allow tearing": "Permitir tearing",
    "Angle Offset": "Desplazamiento del ángulo",
    "Animate manual resizes": "Animar redimensionados manuales",
    "Animate mouse window dragging": "Animar el arrastre de ventanas con el ratón",
    "Animate windows being dragged by the mouse": "Animar las ventanas arrastradas con el ratón",
    "Animate windows when manually resizing or moving them": (
        "Animar las ventanas al redimensionarlas o moverlas manualmente"
    ),
    "Appropriate XKB keymap parameter": "Parámetro de mapa de teclas XKB adecuado",
    "Auto": "Automático",
    "Base Magnification": "Ampliación base",
    "Before active": "Antes del activo",
    "Blur": "Desenfoque",
    "Blur brightness": "Brillo del desenfoque",
    "Blur contrast": "Contraste del desenfoque",
    "Blur noise": "Ruido del desenfoque",
    "Blur passes": "Pasadas de desenfoque",
    "Blur popups": "Desenfocar ventanas emergentes",
    "Blur radius in pixels": "Radio del desenfoque en píxeles",
    "Blur size": "Tamaño del desenfoque",
    "Blur special workspaces": "Desenfocar escritorios especiales",
    "Blur vibrancy": "Vibrancia del desenfoque",
    "Blur vibrancy darkness": "Oscuridad de la vibrancia del desenfoque",
    "Border Colors": "Colores del borde",
    "Border color for inactive windows": "Color del borde de ventanas inactivas",
    "Border color for the active window": "Color del borde de la ventana activa",
    "Border overlap": "Solapamiento del borde",
    "Border size": "Tamaño del borde",
    "Borders": "Bordes",
    "Bottom": "Abajo",
    "Brightness modulation for blur": "Modulación de brillo para el desenfoque",
    "Cancel ratio": "Proporción de cancelación",
    "Center": "Centro",
    "Clickfinger behavior": "Comportamiento de clic con dedos",
    "Clockwise offset applied to the angle in degrees.": (
        "Desplazamiento en sentido horario aplicado al ángulo en grados."
    ),
    "Columns": "Columnas",
    "Contrast modulation for blur": "Modulación de contraste para el desenfoque",
    "Controls adaptive sync globally": "Controla la sincronización adaptativa globalmente",
    "Controls how much the cursor is stretched.": "Controla cuánto se estira el cursor.",
    "Controls how powerful the tilt is, the lower, the more power.": (
        "Controla la intensidad de la inclinación; cuanto menor, más intensa."
    ),
    "Controls how soon a shake is detected.": "Controla con qué rapidez se detecta un agitado.",
    "Corner rounding": "Redondeo de esquinas",
    "Create new workspace": "Crear nuevo escritorio",
    "Cursor behaviour mode": "Modo de comportamiento del cursor",
    "Decoration": "Decoración",
    "Default column width": "Ancho de columna predeterminado",
    "Default placement of the master area": "Ubicación predeterminada del área maestra",
    "Default split ratio": "Proporción de división predeterminada",
    "Default width of a column as a fraction of the screen": (
        "Ancho predeterminado de una columna como fracción de la pantalla"
    ),
    "Delay before a held-down key is repeated, in milliseconds.": (
        "Retardo antes de que se repita una tecla mantenida, en milisegundos."
    ),
    "Devices": "Dispositivos",
    "Dim around": "Atenuar alrededor",
    "Dim factor for floating windows when a dialog is open": (
        "Factor de atenuación de ventanas flotantes cuando hay un diálogo abierto"
    ),
    "Dim inactive": "Atenuar inactivas",
    "Dim special": "Atenuar especiales",
    "Dim strength": "Intensidad de atenuación",
    "Dim the rest of the screen when a special workspace is open": (
        "Atenúa el resto de la pantalla cuando hay un escritorio especial abierto"
    ),
    "Direction in which new windows appear and the layout scrolls": (
        "Dirección en la que aparecen las ventanas nuevas y se desplaza la distribución"
    ),
    "Direction lock": "Bloqueo de dirección",
    "Disable Hyprland logo": "Desactivar el logotipo de Hyprland",
    "Disable autoreload": "Desactivar recarga automática",
    "Disable config autoreload (manual reload via hyprctl reload)": (
        "Desactiva la recarga automática de la configuración (recarga manual con hyprctl reload)"
    ),
    "Disable cursor warps": "Desactivar saltos del cursor",
    "Disable donation nag": "Desactivar el aviso de donaciones",
    "Disable splash rendering": "Desactivar la pantalla de inicio",
    "Disable the donation popup that shows twice a year": (
        "Desactiva la ventana emergente de donaciones que aparece dos veces al año"
    ),
    "Disable the popup when Hyprland is updated to a new version": (
        "Desactiva la ventana emergente cuando Hyprland se actualiza a una versión nueva"
    ),
    "Disable update news": "Desactivar novedades de actualización",
    "Disable while typing": "Desactivar al escribir",
    "Disabled": "Desactivado",
    "Disables hardware cursors.": "Desactiva los cursores por hardware.",
    "Disables the random Hyprland logo / anime girl background. :(": (
        "Desactiva el fondo aleatorio del logotipo de Hyprland. :("
    ),
    "Display Settings": "Ajustes de pantalla",
    "Distance in pixels for the touchpad swipe gesture": (
        "Distancia en píxeles para el gesto de deslizamiento del touchpad"
    ),
    "Do not warp the cursor when focusing, using keybinds, etc.": (
        "No saltar el cursor al enfocar, usar atajos, etc."
    ),
    "Drag lock": "Bloqueo de arrastre",
    "Drag lock mode for tap-and-drag": "Modo de bloqueo de arrastre para tocar y arrastrar",
    "Dwindle": "Dwindle",
    "Ecosystem": "Ecosistema",
    "Enable Hyprcursor": "Activar Hyprcursor",
    "Enable Hyprcursor Support": "Activar compatibilidad con Hyprcursor",
    "Enable IPC Events": "Activar eventos IPC",
    "Enable Shake to Find": "Activar agitar para encontrar",
    "Enable XWayland": "Activar XWayland",
    "Enable adding additional master windows in a horizontal split style": (
        "Permite añadir ventanas maestras adicionales en una división horizontal"
    ),
    "Enable animations": "Activar animaciones",
    "Enable blur": "Activar desenfoque",
    "Enable drop shadows on windows": "Activar sombras paralelas en las ventanas",
    "Enable further optimizations to the blur (recommended)": (
        "Activa más optimizaciones del desenfoque (recomendado)"
    ),
    "Enable kawase window background blur": "Activar el desenfoque de fondo Kawase",
    "Enable natural (inverted) scrolling": "Activar desplazamiento natural (invertido)",
    "Enable natural scrolling on touchpad": "Activar desplazamiento natural en el touchpad",
    "Enable numlock on startup": "Activar Bloq Num al iniciar",
    "Enable shadow": "Activar sombra",
    "Enable snap": "Activar ajuste",
    "Enable snapping for floating windows": "Activar ajuste para ventanas flotantes",
    "Enable workspace swipe via touchscreen": (
        "Activar deslizamiento de escritorios con pantalla táctil"
    ),
    "Enabled (also unfocuses)": "Activado (también desenfoca)",
    "Enabled with timeout": "Activado con tiempo de espera",
    "Enables dimming of inactive windows": "Activa la atenuación de ventanas inactivas",
    "Enables resizing windows by clicking and dragging on borders and gaps": (
        "Permite redimensionar ventanas haciendo clic y arrastrando en bordes y márgenes"
    ),
    "Enables the plugin": "Activa el plugin",
    "Enforce permissions": "Aplicar permisos",
    "Extend border grab area": "Ampliar el área de agarre del borde",
    "Extends the area around the border where you can click and drag for resize": (
        "Amplía el área alrededor del borde donde puedes hacer clic y arrastrar para redimensionar"
    ),
    "Fallback Shape": "Forma de reserva",
    "Fit": "Ajustar",
    "Flat": "Plano",
    "Float switch override focus": "El cambio a flotante anula el enfoque",
    "Focus": "Enfoque",
    "Focus fit method": "Método de ajuste de enfoque",
    "Focus on activate": "Enfocar al activar",
    "Focus the window under cursor when switching between tiled and floating": (
        "Enfoca la ventana bajo el cursor al alternar entre mosaico y flotante"
    ),
    "Follow focus": "Seguir el enfoque",
    "Follow mouse": "Seguir al ratón",
    "Force a split direction for new windows": (
        "Forzar una dirección de división para ventanas nuevas"
    ),
    "Force apps to use Wayland-native scaling instead of X11 scaling": (
        "Obliga a las aplicaciones a usar escalado nativo de Wayland en lugar del de X11"
    ),
    "Force default wallpaper": "Forzar fondo de pantalla predeterminado",
    "Force split direction": "Forzar dirección de división",
    "Force the default wallpaper (-1 random, 0-2 for specific)": (
        "Fuerza el fondo de pantalla predeterminado (-1 aleatorio, 0-2 para uno concreto)"
    ),
    "Force zero scaling": "Forzar escalado cero",
    "Full Tilt Angle": "Ángulo de inclinación completo",
    "Full tilt for each side (°).": "Inclinación completa para cada lado (°).",
    "Fullscreen (game content)": "Pantalla completa (contenido de juego)",
    "Fullscreen only": "Solo pantalla completa",
    "Fullscreen opacity": "Opacidad en pantalla completa",
    "Gaps between windows and monitor edges in pixels": (
        "Márgenes entre ventanas y bordes del monitor en píxeles"
    ),
    "Gaps between windows in pixels": "Márgenes entre ventanas en píxeles",
    "Gestures": "Gestos",
    "Hardware cursors": "Cursores por hardware",
    "Hide on key press": "Ocultar al pulsar una tecla",
    "Hide on tablet": "Ocultar con tableta",
    "Hide on touch": "Ocultar al tocar",
    "Hide the splash text shown on the desktop after startup": (
        "Oculta el texto de inicio que aparece en el escritorio tras arrancar"
    ),
    "Hides the cursor when the last input was a tablet input until a mouse input is done.": (
        "Oculta el cursor cuando la última entrada fue de tableta hasta que se usa el ratón."
    ),
    "Hides the cursor when the last input was a touch input until a mouse input is done.": (
        "Oculta el cursor cuando la última entrada fue táctil hasta que se usa el ratón."
    ),
    "Hides the cursor when you press any key until the mouse is moved.": (
        "Oculta el cursor al pulsar cualquier tecla hasta que se mueve el ratón."
    ),
    "Higher values produce a faster shadow falloff": (
        "Los valores altos producen una caída de sombra más rápida"
    ),
    "Hover icon on border": "Mostrar icono al pasar por el borde",
    "How cursor speed changes relative to mouse movement speed": (
        "Cómo cambia la velocidad del cursor respecto a la del ratón"
    ),
    "How far you need to swipe before the gesture commits": (
        "Cuánto hay que deslizar antes de que el gesto se confirme"
    ),
    "How much noise to apply to the blur": "Cuánto ruido aplicar al desenfoque",
    "How much to dim inactive windows": "Cuánto atenuar las ventanas inactivas",
    "How much to increase saturation of dark colors": (
        "Cuánto aumentar la saturación de los colores oscuros"
    ),
    "Hypr Dynamic Cursors": "Cursores dinámicos de Hypr",
    "Hyprcursor": "Hyprcursor",
    "If DPMS is set to off, wake up the monitors if a key is pressed.": (
        "Si DPMS está desactivado, despierta los monitores al pulsar una tecla."
    ),
    "If DPMS is set to off, wake up the monitors if the mouse moves": (
        "Si DPMS está desactivado, despierta los monitores al mover el ratón"
    ),
    "If enabled, floating windows will ignore tiled windows in their blur": (
        "Si está activado, las ventanas flotantes ignoran las de mosaico en su desenfoque"
    ),
    "If enabled, makes the preselect direction persist.": (
        "Si está activado, la dirección preseleccionada se mantiene."
    ),
    "If enabled, resizing direction will be determined by the mouse's position on the window.": (
        "Si está activado, la dirección de redimensionado la determina la"
        " posición del ratón en la ventana."
    ),
    "If true, snapping will respect gaps between windows": (
        "Si se activa, el ajuste respetará los márgenes entre ventanas"
    ),
    "If true, windows snap such that only one border's worth of space is between them": (
        "Si se activa, las ventanas se ajustan dejando solo el espacio de un borde entre ellas"
    ),
    "Ignore opacity": "Ignorar opacidad",
    "Ignore touchpad input while typing on the keyboard": (
        "Ignorar la entrada del touchpad mientras se escribe en el teclado"
    ),
    "Inactive border color": "Color del borde inactivo",
    "Inactive opacity": "Opacidad inactiva",
    "Inactive shadow color": "Color de sombra inactiva",
    "Inactive shadow color. (if not set, will fall back to col.shadow)": (
        "Color de sombra inactiva. (si no se define, se usa col.shadow)"
    ),
    "Inactive timeout": "Tiempo de espera de inactividad",
    "Increase saturation of blurred colors": "Aumentar la saturación de los colores desenfocados",
    "Inherit": "Heredar",
    "Inner gaps": "Márgenes internos",
    "Invert direction": "Invertir dirección",
    "Invert the direction (touchpad only)": "Invertir la dirección (solo touchpad)",
    "Invert the direction (touchscreen only)": "Invertir la dirección (solo pantalla táctil)",
    "Invert touch direction": "Invertir la dirección táctil",
    "Keep swiping through workspaces without limits": (
        "Seguir deslizando por los escritorios sin límite"
    ),
    "Key press enables DPMS": "Una tecla activa DPMS",
    "Keyboard": "Teclado",
    "Keyboard options": "Opciones de teclado",
    "Keyboard variant": "Variante de teclado",
    "Layout": "Distribución",
    "Left / Top": "Izquierda / Arriba",
    "Left, Middle, Right": "Izquierda, central, derecha",
    "Left, Right, Middle": "Izquierda, derecha, central",
    "Left-handed mode": "Modo para zurdos",
    "Length in px of the simulated stick used to rotate the cursor.": (
        "Longitud en píxeles del stick simulado usado para rotar el cursor."
    ),
    "Lock gesture to a single direction once started": (
        "Bloquear el gesto a una sola dirección una vez iniciado"
    ),
    "Loose": "Suelto",
    "Loose (no mouse focus)": "Suelto (sin enfoque por ratón)",
    "Magnification Limit": "Límite de ampliación",
    "Magnification Speed": "Velocidad de ampliación",
    "Magnifies the cursor if it is being shaken.": "Amplía el cursor si se agita.",
    "Make the blur layer ignore the opacity of the window": (
        "Hace que la capa de desenfoque ignore la opacidad de la ventana"
    ),
    "Master": "Maestro",
    "Master factor": "Factor maestro",
    "Master switch for all window and workspace animations": (
        "Interruptor general para todas las animaciones de ventanas y escritorios"
    ),
    "Middle button emulation": "Emulación del botón central",
    "Min speed to force": "Velocidad mínima para forzar",
    "Minimum angle difference in degrees after which the shape is changed.": (
        "Diferencia mínima de ángulo en grados tras la cual cambia la forma."
    ),
    "Minimum gap in pixels between window and monitor edges before snapping": (
        "Margen mínimo en píxeles entre la ventana y los bordes del monitor antes de ajustar"
    ),
    "Minimum gap in pixels between windows before snapping": (
        "Margen mínimo en píxeles entre ventanas antes de ajustar"
    ),
    "Minimum swipe speed to force the gesture regardless of distance": (
        "Velocidad mínima de deslizamiento para forzar el gesto sin importar la distancia"
    ),
    "Minimum visible fraction": "Fracción visible mínima",
    "Miscellaneous": "Varios",
    "Modifier keys, layout switching, and other keyboard tweaks": (
        "Teclas modificadoras, cambio de distribución y otros ajustes de teclado"
    ),
    "Monitor snap gap": "Margen de ajuste del monitor",
    "Monocle": "Monocle",
    "Mouse": "Ratón",
    "Mouse move enables DPMS": "Mover el ratón activa DPMS",
    "Mouse refocus": "Reenfocar con el ratón",
    "Mouse sensitivity": "Sensibilidad del ratón",
    "Mouse sensitivity (-1.0 to 1.0, 0 is default)": (
        "Sensibilidad del ratón (-1.0 a 1.0, 0 es el valor predeterminado)"
    ),
    (
        "Move cursor to the last focused window after switching"
        " workspaces. Force ignores the no_warps setting."
    ): (
        "Mueve el cursor a la última ventana enfocada tras cambiar de"
        " escritorio. Force ignora el ajuste no_warps."
    ),
    "Move the layout automatically to bring the focused window into view": (
        "Desplaza la distribución automáticamente para mostrar la ventana enfocada"
    ),
    "Multiplier applied to the amount of scroll movement.": (
        "Multiplicador aplicado a la cantidad de desplazamiento."
    ),
    "Multiplier for scroll amount": "Multiplicador del desplazamiento",
    "Natural scroll": "Desplazamiento natural",
    "New on top": "Nuevas arriba",
    "New optimizations": "Nuevas optimizaciones",
    "New window placement": "Ubicación de ventanas nuevas",
    "New window status": "Estado de ventanas nuevas",
    "None": "Ninguno",
    "Numlock by default": "Bloq Num por defecto",
    "Opacity of active windows": "Opacidad de ventanas activas",
    "Opacity of fullscreen windows": "Opacidad de ventanas en pantalla completa",
    "Opacity of inactive windows": "Opacidad de ventanas inactivas",
    "Orientation": "Orientación",
    "Outer gaps": "Márgenes externos",
    "Permanent direction override": "Anulación permanente de dirección",
    "Persistent warps": "Saltos persistentes",
    "Pixelated Scaling": "Escalado pixelado",
    "Preserve split": "Preservar división",
    "Preset column widths": "Anchos de columna predefinidos",
    "Press left and right buttons simultaneously to emulate middle click": (
        "Pulsa los botones izquierdo y derecho a la vez para emular el clic central"
    ),
    "Re-focus the window under the cursor when switching workspaces": (
        "Reenfoca la ventana bajo el cursor al cambiar de escritorio"
    ),
    "Relationship between speed and stretch amount.": (
        "Relación entre la velocidad y la cantidad de estiramiento."
    ),
    "Relationship between speed and tilt.": "Relación entre la velocidad y la inclinación.",
    "Remember cursor position per window when warping back": (
        "Recordar la posición del cursor por ventana al volver"
    ),
    "Repeat delay": "Retardo de repetición",
    "Repeat rate": "Frecuencia de repetición",
    "Resize on border": "Redimensionar en el borde",
    "Respect gaps": "Respetar márgenes",
    "Right / Bottom": "Derecha / Abajo",
    "Rotate Settings": "Ajustes de rotación",
    "Rounded corners' radius (in layout px)": (
        "Radio de las esquinas redondeadas (en px de distribución)"
    ),
    "Rounding and Opacity": "Redondeo y opacidad",
    "Rounding power": "Potencia de redondeo",
    "Rounding power of corners (2 is a circle)": (
        "Potencia de redondeo de las esquinas (2 es un círculo)"
    ),
    "Scale factor for the shadow relative to the window": (
        "Factor de escala de la sombra respecto a la ventana"
    ),
    "Scroll direction": "Dirección de desplazamiento",
    "Scroll factor": "Factor de desplazamiento",
    "Scrolling": "Desplazamiento",
    "Seconds before hiding cursor (0 to never hide)": (
        "Segundos antes de ocultar el cursor (0 para no ocultarlo nunca)"
    ),
    "Sets the tap button mapping for touchpad button emulation. [lrm/lmr]": (
        "Define la asignación de botones al tocar para la emulación de botones"
        " del touchpad. [lrm/lmr]"
    ),
    "Shadow color": "Color de sombra",
    "Shadow offset": "Desplazamiento de sombra",
    "Shadow position offset as 'x y' in pixels": (
        "Desplazamiento de posición de la sombra como 'x y' en píxeles"
    ),
    "Shadow range": "Alcance de la sombra",
    "Shadow range (size) in layout px": "Alcance (tamaño) de la sombra en px de distribución",
    "Shadow render power": "Potencia de renderizado de la sombra",
    "Shadow scale": "Escala de la sombra",
    "Shadow's color. Alpha dictates shadow's opacity.": (
        "Color de la sombra. El alfa determina su opacidad."
    ),
    "Shake Influence": "Influencia del agitado",
    "Shake to Find": "Agitar para encontrar",
    "Show Effects During Shake": "Mostrar efectos durante el agitado",
    "Show a cursor icon when hovering on borders": (
        "Mostrar un icono de cursor al pasar por los bordes"
    ),
    "Simulated Stick Length": "Longitud del stick simulado",
    "Single column fullscreen": "Pantalla completa en una columna",
    "Size of the border around windows": "Tamaño del borde alrededor de las ventanas",
    "Size of the master area as a percentage of the screen": (
        "Tamaño del área maestra como porcentaje de la pantalla"
    ),
    "Slave": "Esclavo",
    "Smart resizing": "Redimensionado inteligente",
    "Smart split": "División inteligente",
    "Snap": "Ajuste",
    "Special workspace scale": "Escala del escritorio especial",
    "Specifies the auto-split width multiplier": (
        "Especifica el multiplicador de ancho de división automática"
    ),
    "Specifies the scale factor of windows on the special workspace": (
        "Especifica el factor de escala de las ventanas del escritorio especial"
    ),
    "Split Behavior": "Comportamiento de división",
    "Split direction is based on cursor position within the window": (
        "La dirección de división se basa en la posición del cursor dentro de la ventana"
    ),
    "Split direction won't change when the container is resized": (
        "La dirección de división no cambia al redimensionar el contenedor"
    ),
    "Split width multiplier": "Multiplicador de ancho de división",
    "Sticky": "Fijo",
    "Stretch Limit": "Límite de estiramiento",
    "Stretch Settings": "Ajustes de estiramiento",
    "Swipe distance": "Distancia de deslizamiento",
    "Swipe forever": "Deslizar sin límite",
    "Swipe through relative workspaces instead of absolute": (
        "Deslizar por escritorios relativos en lugar de absolutos"
    ),
    "Swiping past the last workspace creates a new one": (
        "Deslizar más allá del último escritorio crea uno nuevo"
    ),
    "Switches RMB and LMB": "Intercambia los botones derecho e izquierdo",
    "Tap button map": "Asignación de botones al tocar",
    "Tap to click": "Tocar para hacer clic",
    "Tapping sends a click (1, 2, or 3 fingers for LMB, RMB, MMB)": (
        "Tocar envía un clic (1, 2 o 3 dedos para botón izquierdo, derecho o central)"
    ),
    "The amount of passes to perform": "Número de pasadas a realizar",
    "The default split ratio on window open.": (
        "Proporción de división predeterminada al abrir una ventana."
    ),
    "The factor to zoom by around the cursor. 1 means no zoom.": (
        "Factor de zoom alrededor del cursor. 1 significa sin zoom."
    ),
    "The repeat rate for held-down keys, in repeats per second.": (
        "Frecuencia de repetición de teclas mantenidas, en repeticiones por segundo."
    ),
    "The scale of the special workspace windows.": (
        "Escala de las ventanas del escritorio especial."
    ),
    "These options only take effect when Hyprland starts": (
        "Estas opciones solo surten efecto al iniciar Hyprland"
    ),
    "Threshold": "Umbral",
    "Tilt Limit": "Límite de inclinación",
    "Tilt Settings": "Ajustes de inclinación",
    "Time Window": "Ventana de tiempo",
    "Time window (ms) over which the speed is calculated.": (
        "Ventana de tiempo (ms) durante la que se calcula la velocidad."
    ),
    "Timeout (ms)": "Tiempo de espera (ms)",
    "Top": "Arriba",
    "Touch swipe": "Deslizamiento táctil",
    "Touchpad": "Touchpad",
    "Touchpad scroll factor": "Factor de desplazamiento del touchpad",
    "Touchscreen": "Pantalla táctil",
    "Use active for splits": "Usar la ventana activa para las divisiones",
    "Use finger count instead of button areas for click mapping": (
        "Usar el número de dedos en lugar de áreas de botones para asignar clics"
    ),
    "Use nearest neighbor": "Usar vecino más cercano",
    "Use nearest-neighbor filtering for upscaling XWayland apps": (
        "Usa filtrado de vecino más cercano al ampliar aplicaciones XWayland"
    ),
    "Use relative workspaces": "Usar escritorios relativos",
    "Variable refresh rate": "Frecuencia de refresco variable",
    "Visibility": "Visibilidad",
    "Warp on workspace change": "Saltar al cambiar de escritorio",
    "When a column is focused, what method should be used to bring it into view": (
        "Cuando se enfoca una columna, qué método usar para mostrarla"
    ),
    (
        "When a window is focused, require that at least a given fraction"
        " of it is visible for focus to follow"
    ): (
        "Al enfocar una ventana, exige que al menos una fracción concreta sea"
        " visible para que el enfoque la siga"
    ),
    "When enabled, a single column on a workspace will always span the entire screen.": (
        "Si está activado, una sola columna en un escritorio siempre ocupa toda la pantalla."
    ),
    "Whether a newly open window should be on the top of the stack": (
        "Si una ventana recién abierta debe estar en la parte superior de la pila"
    ),
    "Whether to blur behind the special workspace (note: expensive)": (
        "Si desenfocar detrás del escritorio especial (nota: costoso)"
    ),
    "Whether to blur popups (e.g. right-click menus)": (
        "Si desenfocar las ventanas emergentes (por ejemplo, menús contextuales)"
    ),
    "Whether to enable hyprcursor support": "Si activar la compatibilidad con hyprcursor",
    "Whether to enable permission control.": "Si activar el control de permisos.",
    "Whether to focus an app that requests to be focused": (
        "Si enfocar una aplicación que lo solicita"
    ),
    "Whether to prefer the active window or the mouse position for splits": (
        "Si preferir la ventana activa o la posición del ratón para las divisiones"
    ),
    "Which layout to use for tiling": "Qué distribución usar para el mosaico",
    "Window focus follows mouse movement": "El enfoque de ventana sigue al movimiento del ratón",
    "Window snap gap": "Margen de ajuste de ventanas",
    "Workspace Swipe": "Deslizamiento de escritorio",
    "X-Ray": "X-Ray",
    "XWayland": "XWayland",
    "Zoom factor": "Factor de zoom",
    (
        "`before`, `after`: place new window relative to the focused"
        " window; `none`: place new window according to new_on_top."
    ): (
        "«before», «after»: colocan la ventana nueva respecto a la enfocada;"
        " «none»: la colocan según new_on_top."
    ),
    (
        "`master`: new window becomes master; `slave`: new windows are"
        " added to slave stack; `inherit`: inherit from focused window"
    ): (
        "«master»: la ventana nueva pasa a ser maestra; «slave»: se añade a la"
        " pila esclava; «inherit»: hereda de la ventana enfocada"
    ),
    "disable": "desactivar",
    "enable": "activar",
    "force": "forzar",
    "none": "ninguno",
    # -- Additional UI strings (second pass) --
    "(disabled)": "(desactivado)",
    "(no action)": "(sin acción)",
    "(no overrides)": "(sin modificaciones)",
    "(no rule)": "(sin regla)",
    "(no shortcut)": "(sin atajo)",
    "(none)": "(ninguno)",
    "(untitled)": "(sin título)",
    ("0 = below lock; 1 = above input; 2 = above everything."): (
        "0 = bajo el bloqueo; 1 = sobre los campos de entrada; 2 = sobre todo."
    ),
    "1 keybind": "1 atajo",
    "1 option selected": "1 opción seleccionada",
    "10-bit": "10 bits",
    "180°": "180°",
    "270°": "270°",
    "8-bit": "8 bits",
    "90°": "90°",
    "Add Anyway": "Añadir de todos modos",
    "Add Keybind…": "Añadir atajo…",
    "Add Rule…": "Añadir regla…",
    "Add Variable…": "Añadir variable…",
    "Add another entry that runs {label}": "Añadir otra entrada que ejecute {label}",
    ("Add arbitrary settings for unsupported plugins."): (
        "Añade ajustes arbitrarios para plugins no compatibles."
    ),
    "Add keybind to {category}": "Añadir atajo a {category}",
    ("Add programs that should launch automatically when Hyprland starts."): (
        "Añade programas que deben iniciarse automáticamente al arrancar Hyprland."
    ),
    "Adobe": "Adobe",
    (
        "Allow this layer to render above the session lock. 0 = below (default), 1 = above "
        "input fields, 2 = above everything."
    ): (
        "Permite que esta capa se dibuje por encima del bloqueo de sesión. 0 = debajo "
        "(predeterminado), 1 = sobre los campos de entrada, 2 = sobre todo."
    ),
    ("Also blur popup surfaces spawned above this layer."): (
        "También desenfoca las superficies emergentes que se abren por encima de esta capa."
    ),
    "Animation style": "Estilo de animación",
    "Applied to 1 existing window": "Aplicado a 1 ventana existente",
    "Applied to {count} existing windows": "Aplicado a {count} ventanas existentes",
    ("Applies instantly to Hyprland and GTK apps. Other apps pick up changes on relaunch."): (
        "Se aplica al instante en Hyprland y las apps GTK. Otras apps recogen los cambios al "
        "reiniciarse."
    ),
    "Apply Live": "Aplicar en vivo",
    ("Apply backdrop blur behind this layer surface (e.g. waybar, rofi)."): (
        "Aplica desenfoque de fondo tras esta superficie de capa (p. ej. waybar, rofi)."
    ),
    ("Automatically save changes after each modification."): (
        "Guarda los cambios automáticamente tras cada modificación."
    ),
    "Bezier easing curve": "Curva de aceleración Bézier",
    ("Bind keys to launch apps, switch workspaces, or trigger any Hyprland dispatcher."): (
        "Asigna teclas para lanzar apps, cambiar de espacio de trabajo o ejecutar cualquier "
        "dispatcher de Hyprland."
    ),
    "Bind type": "Tipo de atajo",
    "Blur background": "Desenfocar el fondo",
    "Brightness for SDR content in HDR mode": "Brillo del contenido SDR en modo HDR",
    "Cannot move config: {error}": "No se puede mover la configuración: {error}",
    "Category": "Categoría",
    "Center on monitor": "Centrar en el monitor",
    ("Center the window on its monitor (floating only)."): (
        "Centra la ventana en su monitor (solo flotantes)."
    ),
    "Center window": "Centrar ventana",
    ("Class at spawn time, before the app sets a different one. Regex."): (
        "Clase al abrirse, antes de que la app establezca otra. Regex."
    ),
    "Click any mouse button…": "Haz clic con cualquier botón del ratón…",
    "Clone another monitor's content": "Duplicar el contenido de otro monitor",
    "Close window": "Cerrar ventana",
    ("Could not read monitor information from Hyprland."): (
        "No se pudo leer la información de monitores desde Hyprland."
    ),
    ("Couldn't apply to existing windows: {error}"): (
        "No se pudo aplicar a las ventanas existentes: {error}"
    ),
    "Couldn't parse command: {error}": "No se pudo interpretar el comando: {error}",
    "Couldn't start capture - {error}": "No se pudo iniciar la captura - {error}",
    "Custom Command…": "Comando personalizado…",
    "Custom button (e.g. mouse:277)": "Botón personalizado (p. ej. mouse:277)",
    "Cycle focus next": "Pasar el foco a la siguiente",
    "Cycle group member": "Rotar el miembro del grupo",
    "Default: {path}": "Predeterminado: {path}",
    "Deny window from group": "Excluir la ventana del grupo",
    "Dim everything else": "Atenuar el resto",
    (
        "Dim the background while this surface is mapped. Typical for app launchers like rofi "
        "or wofi."
    ): (
        "Atenúa el fondo mientras esta superficie está visible. Habitual en lanzadores como "
        "rofi o wofi."
    ),
    ("Disable background blur behind this window."): (
        "Desactiva el desenfoque de fondo tras esta ventana."
    ),
    ("Disable open/close animations for this surface."): (
        "Desactiva las animaciones de apertura y cierre de esta superficie."
    ),
    ("Disable open/close animations for this window."): (
        "Desactiva las animaciones de apertura y cierre de esta ventana."
    ),
    "Disable the drop shadow for this window.": "Desactiva la sombra de esta ventana.",
    "Disable transparency for this window.": "Desactiva la transparencia de esta ventana.",
    "Display scaling factor": "Factor de escala de pantalla",
    ("Don't auto-destroy the workspace when its last window closes."): (
        "No destruyas automáticamente el espacio de trabajo al cerrarse su última ventana."
    ),
    "Don't focus the window when it spawns.": "No enfoca la ventana al abrirse.",
    "Duplicate keybind": "Atajo duplicado",
    "Duration in deciseconds (1 = 100ms)": "Duración en decisegundos (1 = 100 ms)",
    "EDID": "EDID",
    "Enter submap": "Entrar en submapa",
    "Event": "Evento",
    "Exclude from screen share": "Excluir de la compartición de pantalla",
    "Exit Hyprland": "Salir de Hyprland",
    (
        "Export variables to processes Hyprland spawns - toolkit hints (QT_QPA_PLATFORM), "
        "theme overrides, scaling settings, and so on."
    ): (
        "Exporta variables a los procesos que lanza Hyprland: indicaciones de toolkit "
        "(QT_QPA_PLATFORM), ajustes de tema, escalado, etc."
    ),
    "Failed to run: {error}": "Error al ejecutar: {error}",
    "Flipped": "Volteado",
    "Flipped 180°": "Volteado 180°",
    "Flipped 270°": "Volteado 270°",
    "Flipped 90°": "Volteado 90°",
    "Float window": "Flotar ventana",
    "Floating": "Flotante",
    "Focus and Move Windows": "Enfocar y mover ventanas",
    "Focus last window": "Enfocar la última ventana",
    "Focus monitor": "Enfocar monitor",
    "Focus urgent/last": "Enfocar urgente/última",
    "Focus workspace on current monitor": "Enfocar espacio de trabajo en el monitor actual",
    "Focused": "Enfocada",
    "Force kill window": "Forzar el cierre de la ventana",
    "Force opaque": "Forzar opacidad",
    "Force the window into the tiling layout.": "Fuerza la ventana al diseño en mosaico.",
    "Fullscreen": "Pantalla completa",
    "Fullscreen + Gaming": "Pantalla completa + juegos",
    "Global shortcut": "Atajo global",
    "Go Back": "Volver",
    "Got it": "Entendido",
    "HDR": "HDR",
    "HDR (EDID)": "HDR (EDID)",
    (
        "Hide this surface from screen-sharing captures. Useful for notification daemons or "
        "password prompts."
    ): (
        "Oculta esta superficie en las capturas de compartición de pantalla. Útil para daemons "
        "de notificaciones o avisos de contraseña."
    ),
    "How this binding is activated": "Cómo se activa este atajo",
    (
        "Hyprland is still running with your existing hyprland.conf - the config language is "
        "fixed at compositor startup, so the new hyprland.lua won't take over until you log "
        "out and log back in.\n\nUntil then, live-preview changes in HyprMod still go through "
        "the .conf path and may behave inconsistently with the new .lua files on disk."
    ): (
        "Hyprland sigue ejecutándose con tu hyprland.conf existente: el lenguaje de "
        "configuración se fija al iniciar el compositor, así que el nuevo hyprland.lua no "
        "tomará el relevo hasta que cierres sesión y vuelvas a entrar.\n\nMientras tanto, los "
        "cambios de previsualización en vivo de HyprMod siguen pasando por la ruta .conf y "
        "pueden comportarse de forma inconsistente con los nuevos archivos .lua del disco."
    ),
    ("Hyprland will never focus this window (e.g. legacy XWayland helpers)."): (
        "Hyprland nunca enfocará esta ventana (p. ej. ayudantes XWayland antiguos)."
    ),
    ("Hyprland {ver} recommends Lua configs - migrate your hyprland.conf with one click"): (
        "Hyprland {ver} recomienda configuraciones Lua: migra tu hyprland.conf con un clic"
    ),
    "Ignore alpha below threshold": "Ignorar alfa por debajo del umbral",
    "Inactive": "Inactiva",
    "Inhibit idle": "Inhibir inactividad",
    "Initial class": "Clase inicial",
    "Initial title": "Título inicial",
    "Key": "Tecla",
    "Key combination": "Combinación de teclas",
    "Launch Application": "Lanzar aplicación",
    "Layout message": "Mensaje de diseño",
    ("Leave blank to use the active value for both."): (
        "Déjalo vacío para usar el valor activo en ambos."
    ),
    "Left button": "Botón izquierdo",
    "Level": "Nivel",
    "Lock active group": "Bloquear el grupo activo",
    "Lock all groups": "Bloquear todos los grupos",
    "Lock {field} with {peer}": "Bloquear {field} con {peer}",
    "Locked": "Bloqueada",
    (
        "Locked keybinds come from your hyprland.conf. Supported keybinds can be overridden "
        "with the edit button."
    ): (
        "Los atajos bloqueados provienen de tu hyprland.conf. Los atajos compatibles se pueden "
        "sobrescribir con el botón de editar."
    ),
    (
        "Make Hyprland treat specific windows differently - pin them, set opacity, open on a "
        "workspace, and more."
    ): (
        "Haz que Hyprland trate ventanas concretas de otra forma: fíjalas, ajusta la opacidad, "
        "ábrelas en un espacio de trabajo y más."
    ),
    (
        "Make blur look through other windows instead of blurring them. Overrides "
        "‘decoration:blur:xray’ for this surface."
    ): (
        "Hace que el desenfoque atraviese otras ventanas en lugar de desenfocarlas. "
        "Sobrescribe ‘decoration:blur:xray’ para esta superficie."
    ),
    ("Manage where HyprMod reads and writes Hyprland settings."): (
        "Gestiona dónde lee y escribe HyprMod los ajustes de Hyprland."
    ),
    "Manual Edit": "Edición manual",
    ("Match by app class (e.g. firefox, kitty). Regex."): (
        "Coincide por clase de app (p. ej. firefox, kitty). Regex."
    ),
    "Match by description: “{prefix}”": "Coincidencia por descripción: «{prefix}»",
    "Match by window title text. Regex.": "Coincide por el texto del título de la ventana. Regex.",
    "Maximum luminance for HDR output": "Luminancia máxima para la salida HDR",
    "Maximum luminance for SDR content": "Luminancia máxima del contenido SDR",
    "Middle button": "Botón central",
    "Minimum luminance for HDR output": "Luminancia mínima para la salida HDR",
    "Minimum luminance for SDR to HDR mapping": "Luminancia mínima para la conversión de SDR a HDR",
    "Modal": "Modal",
    "Monitor Control": "Control de monitores",
    "Monitor maximum average luminance": "Luminancia media máxima del monitor",
    ("Monitor name (e.g. DP-1) or numeric index."): (
        "Nombre del monitor (p. ej. DP-1) o índice numérico."
    ),
    "Mouse Button": "Botón del ratón",
    "Mouse button": "Botón del ratón",
    "Move focus": "Mover el foco",
    "Move into group": "Mover al grupo",
    "Move specific workspace to monitor": "Mover un espacio de trabajo concreto al monitor",
    "Move window": "Mover ventana",
    "Move window or group": "Mover ventana o grupo",
    "Move window silently": "Mover ventana sin cambiar de espacio",
    "Move window to workspace": "Mover ventana a un espacio de trabajo",
    "Move workspace to monitor": "Mover espacio de trabajo al monitor",
    "Named workspace": "Espacio de trabajo con nombre",
    "Never focusable": "Nunca enfocable",
    "No animations": "Sin animaciones",
    "No blur": "Sin desenfoque",
    "No initial focus": "Sin foco inicial",
    "No shadow": "Sin sombra",
    "Non-consuming": "No consumir",
    "Normal": "Normal",
    "Normal for most keybinds": "Normal para la mayoría de atajos",
    "On workspace": "En el espacio de trabajo",
    "One of: none, always, focus, fullscreen.": "Uno de: none, always, focus, fullscreen.",
    ("One of: slide, popin, fade, none. (Style names depend on Hyprland version.)"): (
        "Uno de: slide, popin, fade, none. (Los nombres de estilo dependen de la versión de "
        "Hyprland.)"
    ),
    ("Open a window in Hyprland and try again."): (
        "Abre una ventana en Hyprland e inténtalo de nuevo."
    ),
    "Open on monitor": "Abrir en el monitor",
    "Open on workspace": "Abrir en el espacio de trabajo",
    ("Open the window detached from the tiling layout."): (
        "Abre la ventana fuera del diseño en mosaico."
    ),
    "Open the window fullscreen.": "Abre la ventana en pantalla completa.",
    "Open the window maximized.": "Abre la ventana maximizada.",
    ("Open this workspace automatically when the monitor connects."): (
        "Abre este espacio de trabajo automáticamente cuando se conecte el monitor."
    ),
    "Order": "Orden",
    (
        "Override active and inactive opacity. Append ' override' to set absolutely (else "
        "multiplied)."
    ): (
        "Sobrescribe la opacidad activa e inactiva. Añade ' override' para establecerla en "
        "absoluto (si no, se multiplica)."
    ),
    ("Override the corner rounding (in pixels) for this window."): (
        "Sobrescribe el redondeo de esquinas (en píxeles) de esta ventana."
    ),
    ("Override the open/close animation for this surface."): (
        "Sobrescribe la animación de apertura y cierre de esta superficie."
    ),
    "Overwrite": "Sobrescribir",
    "Parameters": "Parámetros",
    "Pass key to window": "Pasar la tecla a la ventana",
    "Per side": "Por lado",
    "Pick from Installed Apps": "Elegir entre apps instaladas",
    "Pick from Open Window": "Elegir desde ventana abierta",
    "Pin to all workspaces": "Fijar en todos los espacios de trabajo",
    "Pin window": "Fijar ventana",
    (
        "Pin workspaces to specific monitors, configure persistence, override gaps and "
        "borders, or run a command when a workspace first opens."
    ): (
        "Fija espacios de trabajo a monitores concretos, configura la persistencia, "
        "sobrescribe márgenes y bordes, o ejecuta un comando al abrirse un espacio de trabajo."
    ),
    "Pinned": "Fijada",
    "Pixels": "Píxeles",
    "Press a key combination…": "Pulsa una combinación de teclas…",
    ("Prevent idle/screensaver while this window is around."): (
        "Evita la inactividad y el salvapantallas mientras esta ventana esté abierta."
    ),
    (
        "Profiles let you save and switch between configurations instantly. Save your current "
        "setup to create your first one."
    ): (
        "Los perfiles te permiten guardar y cambiar entre configuraciones al instante. Guarda "
        "tu configuración actual para crear el primero."
    ),
    "Record": "Grabar",
    "Release": "Soltar",
    "Remove from group": "Sacar del grupo",
    "Render order": "Orden de renderizado",
    "Reorder in group": "Reordenar en el grupo",
    "Repeat": "Repetir",
    "Report a Bug": "Informar de un error",
    "Resize window": "Redimensionar ventana",
    "Resolution and refresh rate": "Resolución y frecuencia de actualización",
    "Restore conservative HDR values": "Restaurar valores HDR conservadores",
    ("Reverting in {seconds}s unless changed or confirmed"): (
        "Se revertirá en {seconds} s salvo que se cambie o confirme"
    ),
    "Review deprecated syntax…": "Revisar sintaxis obsoleta…",
    "Right button": "Botón derecho",
    ("Run a shell command the first time this workspace is opened."): (
        "Ejecuta un comando de shell la primera vez que se abra este espacio de trabajo."
    ),
    "Run command": "Ejecutar comando",
    "Run raw command": "Ejecutar comando sin formato",
    "Saturation for SDR content in HDR mode": "Saturación del contenido SDR en modo HDR",
    "Screen on/off": "Pantalla encendida/apagada",
    "Screen rotation": "Rotación de pantalla",
    ("Send the window to a specific monitor on spawn."): (
        "Envía la ventana a un monitor concreto al abrirse."
    ),
    ("Send the window to a specific workspace on spawn."): (
        "Envía la ventana a un espacio de trabajo concreto al abrirse."
    ),
    "Session": "Sesión",
    "Set a custom border width in pixels.": "Establece un ancho de borde personalizado en píxeles.",
    ("Set a custom display name for this workspace."): (
        "Establece un nombre visible personalizado para este espacio de trabajo."
    ),
    "Set corner rounding": "Establecer redondeo de esquinas",
    "Set opacity": "Establecer opacidad",
    "Set position": "Establecer posición",
    "Set size": "Establecer tamaño",
    ("Set the window's initial position. Two space-separated expressions."): (
        "Establece la posición inicial de la ventana. Dos expresiones separadas por espacios."
    ),
    ("Set the window's initial size (floating only)."): (
        "Establece el tamaño inicial de la ventana (solo flotantes)."
    ),
    "Shortcut": "Atajo",
    "Show above lockscreen": "Mostrar sobre la pantalla de bloqueo",
    "Single value": "Valor único",
    (
        "Sort key within a layer; surfaces with higher values render on top. Useful when two "
        "surfaces share a level."
    ): (
        "Clave de orden dentro de una capa; las superficies con valores más altos se dibujan "
        "encima. Útil cuando dos superficies comparten nivel."
    ),
    ("Space-separated: activate, activatefocus, fullscreen, maximize, fullscreenoutput."): (
        "Separado por espacios: activate, activatefocus, fullscreen, maximize, fullscreenoutput."
    ),
    "Special workspace": "Espacio de trabajo especial",
    "Started: {command}": "Iniciado: {command}",
    "Stay focused": "Mantener el foco",
    "Suppress event": "Suprimir evento",
    "Swap window": "Intercambiar ventana",
    "Swap with next": "Intercambiar con la siguiente",
    "Swap workspaces between monitors": "Intercambiar espacios de trabajo entre monitores",
    "Switch workspace": "Cambiar de espacio de trabajo",
    "Tag": "Etiqueta",
    ("Tell Hyprland to ignore a class of events from this window."): (
        "Indica a Hyprland que ignore una clase de eventos de esta ventana."
    ),
    (
        "This key combination is already used by:\n{detail}\n\nHyprland will trigger all matching "
        "binds."
    ): (
        "Esta combinación de teclas ya la usa:\n{detail}\n\nHyprland activará todos los atajos "
        "coincidentes."
    ),
    (
        "This rule's matchers also match HyprMod's own window (class {app_id}). Applying it "
        "live can disrupt this editor - for example, an ‘opacity’ or ‘float’ action would take "
        "effect on the window you are using right now.\n\n‘Save Only’ keeps the rule in your "
        "config and applies it after the next save+reload. ‘Apply Live’ pushes it to the "
        "running compositor immediately."
    ): (
        "Los criterios de esta regla también coinciden con la propia ventana de HyprMod (clase "
        "{app_id}). Aplicarla en vivo puede alterar este editor: por ejemplo, una acción de "
        "«opacity» o «float» afectaría a la ventana que estás usando ahora mismo.\n\n«Guardar "
        "solo» mantiene la regla en tu configuración y la aplica tras el siguiente guardado y "
        "recarga. «Aplicar en vivo» la envía al compositor en ejecución de inmediato."
    ),
    "Tile window": "Mosaicar ventana",
    ("Title at spawn time, before the app updates it. Regex."): (
        "Título al abrirse, antes de que la app lo actualice. Regex."
    ),
    ("To get started, HyprMod needs to add one line to your {entry_name}:"): (
        "Para empezar, HyprMod necesita añadir una línea a tu {entry_name}:"
    ),
    "Toggle floating": "Alternar flotante",
    "Toggle fullscreen": "Alternar pantalla completa",
    "Toggle group": "Alternar grupo",
    "Toggle pseudo-tiling": "Alternar pseudo-mosaico",
    "Toggle scratchpad": "Alternar scratchpad",
    ("Treat pixels below this alpha as not present when computing blur."): (
        "Trata los píxeles por debajo de este alfa como ausentes al calcular el desenfoque."
    ),
    "Trigger": "Activación",
    "Try Again": "Reintentar",
    (
        "Tweak how shell surfaces (waybar, notifications, rofi, wallpapers) are decorated - "
        "backdrop blur, dim-around, animations, render order."
    ): (
        "Ajusta cómo se decoran las superficies de shell (waybar, notificaciones, rofi, "
        "fondos): desenfoque de fondo, atenuación alrededor, animaciones y orden de "
        "renderizado."
    ),
    ("Uncheck to keep the rule defined but inactive on next reload."): (
        "Desmarca para mantener la regla definida pero inactiva al recargar."
    ),
    "Use global": "Usar el global",
    "VRR": "VRR",
    "Wide": "Wide",
    "Window Grouping": "Agrupación de ventanas",
    "Window class": "Clase de ventana",
    ("Window currently has keyboard focus. Re-evaluates dynamically."): (
        "La ventana tiene el foco de teclado ahora mismo. Se reevalúa dinámicamente."
    ),
    "Window has a specific tag.": "La ventana tiene una etiqueta concreta.",
    ("Window is a modal dialog (e.g. 'Are you sure?')."): (
        "La ventana es un diálogo modal (p. ej. «¿Seguro?»)."
    ),
    ("Window is currently floating. Re-evaluates dynamically."): (
        "La ventana es flotante ahora mismo. Se reevalúa dinámicamente."
    ),
    ("Window is currently fullscreen. Re-evaluates dynamically."): (
        "La ventana está en pantalla completa ahora mismo. Se reevalúa dinámicamente."
    ),
    "Window is on a specific workspace.": "La ventana está en un espacio de trabajo concreto.",
    ("Window is pinned across workspaces. Re-evaluates dynamically."): (
        "La ventana está fijada en todos los espacios de trabajo. Se reevalúa dinámicamente."
    ),
    "Window is running under XWayland.": "La ventana se ejecuta bajo XWayland.",
    ("Window keeps focus even when others would steal it."): (
        "La ventana conserva el foco aunque otras intenten robarlo."
    ),
    ("Window stays visible across workspace switches (floating only)."): (
        "La ventana sigue visible al cambiar de espacio de trabajo (solo flotantes)."
    ),
    "Window title": "Título de ventana",
    "Workspace Navigation": "Navegación por espacios de trabajo",
    (
        "Workspace id (e.g. 1) or name (e.g. name:work). Append ' silent' to open without focusing."
    ): (
        "Id del espacio de trabajo (p. ej. 1) o nombre (p. ej. name:work). Añade ' silent' "
        "para abrirlo sin enfocar."
    ),
    "X": "X",
    "XWayland window": "Ventana XWayland",
    "Xray (see-through blur)": "Xray (desenfoque transparente)",
    "Y": "Y",
    "all windows": "todas las ventanas",
    "border": "borde",
    "curve": "curva",
    "default": "predeterminado",
    "gaps in": "márgenes interiores",
    "gaps out": "márgenes exteriores",
    "inherited": "heredado",
    "keybinds": "atajos",
    "mirror": "duplicar",
    "name": "nombre",
    "namespace": "espacio de nombres",
    "no border": "sin borde",
    "no decoration": "sin decoración",
    "no rounding": "sin redondeo",
    "no shadow": "sin sombra",
    "not": "no",
    "off": "desactivado",
    "on": "activado",
    "on first map": "al abrirse",
    "options selected": "opciones seleccionadas",
    "persistent": "persistente",
    "pos": "posición",
    "re-enabled": "reactivado",
    "rotate": "rotar",
    "sRGB": "sRGB",
    "scale": "escala",
    "size": "tamaño",
    "speed": "velocidad",
    "style": "estilo",
    "theme": "tema",
    "updated": "actualizado",
    "workspace {id}": "espacio de trabajo {id}",
    "workspace {name}": "espacio de trabajo {name}",
    "{label} mode": "Modo de {label}",
    "{label} override": "Sobrescritura de {label}",
    ("{match} - ambiguous, another monitor matches the same prefix"): (
        "{match} - ambiguo, otro monitor coincide con el mismo prefijo"
    ),
    ("{message}\n\nFix the file, then try again."): (
        "{message}\n\nCorrige el archivo e inténtalo de nuevo."
    ),
    "{monitor} (disconnected)": "{monitor} (desconectado)",
    "{name} Settings": "Ajustes de {name}",
    ("{name} already exists at this location. It will be replaced with the current config."): (
        "{name} ya existe en esta ubicación. Se reemplazará con la configuración actual."
    ),
    "Add Autostart Entry": "Añadir entrada de inicio automático",
    "Add Environment Variable": "Añadir variable de entorno",
    "Add Plugin Setting": "Añadir ajuste de plugin",
    ("Changes apply instantly via hyprctl: see the effect on your desktop in real time"): (
        "Los cambios se aplican al instante mediante hyprctl: observa el efecto en tu "
        "escritorio en tiempo real"
    ),
    "Choose a new name for this profile": "Elige un nuevo nombre para este perfil",
    "Click to record": "Haz clic para grabar",
    "Ctrl+S to save, Ctrl+F to search, Ctrl+Z to undo": (
        "Ctrl+S para guardar, Ctrl+F para buscar, Ctrl+Z para deshacer"
    ),
    "Edit Autostart Entry": "Editar entrada de inicio automático",
    "Edit Environment Variable": "Editar variable de entorno",
    "Edit Layer Rule": "Editar regla de capa",
    "Edit Plugin Setting": "Editar ajuste de plugin",
    "Edit Window Rule": "Editar regla de ventana",
    "Edit Workspace Rule": "Editar regla de escritorio",
    "Enter a name for the new profile": "Introduce un nombre para el nuevo perfil",
    "Get Started": "Comenzar",
    "Live Preview": "Vista previa en directo",
    "New Layer Rule": "Nueva regla de capa",
    "New Window Rule": "Nueva regla de ventana",
    "New Workspace Rule": "Nueva regla de escritorio",
    "Not Now": "Ahora no",
    "Override Environment Variable": "Anular variable de entorno",
    (
        "PyGObject's Cairo bindings are missing, so the monitor layout, the bezier "
        "curve editor, and the animation preview will show up as empty boxes.\n\n"
        "Install them with your package manager: python-cairo on Arch, "
        "python3-gi-cairo on Debian and Ubuntu."
    ): (
        "Faltan las bibliotecas Cairo de PyGObject, por lo que la distribución de "
        "monitores, el editor de curvas Bézier y la vista previa de animaciones se "
        "mostrarán como cuadros vacíos.\n\n"
        "Instálalas con tu gestor de paquetes: python-cairo en Arch, "
        "python3-gi-cairo en Debian y Ubuntu."
    ),
    "Safe Config": "Configuración segura",
    ("Your {entry_name} is never modified. HyprMod manages its own file"): (
        "Tu {entry_name} nunca se modifica. HyprMod gestiona su propio archivo"
    ),
}


def _(text: str) -> str:
    """Return the translation of *text* in the current language, or the original if missing.

    When a translation contains format placeholders (e.g. ``{count}``),
    call ``.format()`` on the result after translation so the original
    arguments are substituted into the translated string.
    """
    if _current_language != "es":
        return text
    return _CATALOG.get(text, text)


def get_supported_languages() -> dict[str, str]:
    """Return a dict of language codes to their display names."""
    return dict(_SUPPORTED_LANGUAGES)


def set_language(lang_code: str) -> None:
    """Switch the UI language at runtime.

    Supported codes are ``'en'`` (English) and ``'es'`` (Spanish).
    When an unsupported code is passed, English is used as fallback.
    """
    global _current_language
    if lang_code in _SUPPORTED_LANGUAGES:
        _current_language = lang_code
    else:
        _current_language = "en"


def get_current_language() -> str:
    """Return the current language code."""
    return _current_language
