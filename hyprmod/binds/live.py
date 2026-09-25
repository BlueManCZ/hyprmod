"""Convert live Hyprland bind IPC snapshots into the editor's bind shape.

Both helpers run as pure functions over data the page hands in, so they
test in isolation without instantiating any GTK widgets.

- :func:`live_bind_to_data` flattens Hyprland's flag-variant binds
  (``bindm``/``binde``/``bindl``/…) back into the original ``bind_type``
  so overrides round-trip the variant the user actually wrote.
- :func:`enrich_lua_binds` repairs the opaque ``__lua: <line>`` entries
  Hyprland's Lua runtime reports by matching them against the
  hyprland-config Document's parsed bind keywords.
"""

from hyprland_config import BindData, Document, is_bind_keyword, parse_bind_line
from hyprland_socket import Bind, modmask_to_str

OPAQUE_LUA_DISPATCHER = "__lua"


def live_bind_to_data(b: Bind) -> BindData:
    """Convert a Hyprland live :class:`Bind` to a :class:`BindData`.

    Hyprland reports flag-variant binds (``bindm``/``binde``/``bindl``/…)
    as plain ``bind`` entries with boolean flags; this restores the
    original ``bind_type`` so overrides round-trip correctly. For mouse
    binds the runtime also reports ``dispatcher="mouse"`` with the real
    dispatcher in ``arg``, which is unwound here so categorisation works.
    """
    if b.mouse:
        bind_type = "bindm"
    elif b.repeat:
        bind_type = "binde"
    elif b.locked:
        bind_type = "bindl"
    elif b.release:
        bind_type = "bindr"
    elif b.non_consuming:
        bind_type = "bindn"
    else:
        bind_type = "bind"

    # Hyprland's ``bindm`` IPC representation: ``dispatcher="mouse"``
    # with the real dispatcher (``movewindow``/``resizewindow``) in
    # ``arg``.
    if b.mouse and b.dispatcher == "mouse":
        dispatcher = b.arg
        arg = ""
    else:
        dispatcher = b.dispatcher
        arg = b.arg

    return BindData(
        bind_type=bind_type,
        mods=modmask_to_str(b.modmask).split(" + ") if b.modmask else [],
        key=b.key,
        dispatcher=dispatcher,
        arg=arg,
    )


def enrich_lua_binds(live: list[BindData], document: Document) -> list[BindData]:
    """Replace ``__lua`` IPC dispatchers with the real ones from *document*.

    In Lua mode Hyprland reports every bind with ``dispatcher = "__lua"``
    and ``arg = "<lineno>"`` — the runtime stores the bind body as a Lua
    closure with no nameable dispatcher. The hyprland-config Lua reader
    has already walked the user's config and produced ``bind = …``
    keywords with real Hyprlang-style dispatcher names. Match by combo
    (mods + key) and swap the opaque ``__lua`` entry for the rich one
    so the binds page can categorise and label them correctly.

    Live binds without a ``__lua`` dispatcher pass through unchanged.
    Combos that aren't in *document* (handler defined directly via
    ``hl.bind`` with a closure the reader can't unwrap) also pass through
    so the user at least sees that the bind exists. They'll land in
    "Advanced" and read as "Lua callback (read-only)", which is an
    acceptable degradation for an inherently opaque setup.
    """
    if not any(b.dispatcher == OPAQUE_LUA_DISPATCHER for b in live):
        return live
    by_combo: dict[tuple, BindData] = {}
    for kw in document.find_all("bind*"):
        if not is_bind_keyword(kw.key):
            continue
        parsed = parse_bind_line(document.expand(kw.raw.strip()))
        if parsed is not None:
            # Document order means earlier binds win on duplicates,
            # mirroring Hyprland's "first match" runtime behaviour.
            by_combo.setdefault(parsed.combo, parsed)

    enriched: list[BindData] = []
    for b in live:
        if b.dispatcher != OPAQUE_LUA_DISPATCHER:
            enriched.append(b)
            continue
        match = by_combo.get(b.combo)
        if match is None:
            enriched.append(b)
            continue
        # Preserve the live bind's flag-derived bind_type — the document
        # entry's bind_type carries the same info but the live IPC is
        # authoritative for which variant is currently registered.
        enriched.append(
            BindData(
                bind_type=b.bind_type,
                mods=list(b.mods),
                key=b.key,
                dispatcher=match.dispatcher,
                arg=match.arg,
            )
        )
    return enriched
