"""Hyprland v3 ``windowrule`` parser and serializer.

The v3 single-line form looks like::

    windowrule = match:class ^(firefox)$, float on

Key features handled:

- **Match prefix.** Matcher keys carry a ``match:`` prefix and are
  followed by a space (not ``key:value`` with a colon).
- **Required values for boolean effects.** ``float`` alone is invalid
  in 0.53+; we always emit ``on`` for new rules. The set is in
  :data:`V3_BOOL_EFFECTS`.
- **Regex negation** via ``negative:`` value prefix
  (``match:class negative:kitty``).
- **Order is flexible** — matchers and effects can appear in any
  order; we emit matchers first by convention.

Legacy ``windowrulev2`` lines are migrated to v3 *before* reaching
this parser by ``hyprland_config.migrate()`` — see the migration
sites in :func:`hyprmod.core.config.read_all_sections` and
:func:`hyprmod.core.window_rules._external.load_external_window_rules`.
A stray ``windowrulev2`` line that slips through (because a caller
skipped migration) returns ``None`` from the parser rather than being
silently miscompiled.
"""

from hyprmod.core.window_rules._model import (
    KEYWORD_WRITE,
    Matcher,
    WindowRule,
)


def _parse_match_token(token: str) -> Matcher | None:
    """Parse a single ``match:KEY VALUE`` token; return ``None`` on miss."""
    body = token.strip()
    if not body.startswith("match:"):
        return None
    body = body[len("match:") :]
    if not body:
        return None
    key, sep, value = body.partition(" ")
    # In block form Hyprland uses ``match:KEY = VALUE``; we don't write
    # blocks but we can be lenient when reading them flattened — the
    # caller already split on commas, so a stray ``=`` only appears
    # when the user pasted block-style content. Accept it as a fallback.
    if not sep and "=" in body:
        key, _, value = body.partition("=")
    return Matcher(key=key.strip(), value=value.strip())


def _split_top_level(s: str) -> list[str]:
    """Split a windowrule body on top-level commas.

    Top-level meaning: not inside parentheses (used by ``move`` /
    ``size`` expressions like ``cursor_x-(window_w*0.5)``). Square and
    curly brackets are treated as parens too — they're rare in matcher
    values, but the same logic applies.
    """
    result: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in s:
        if ch in "([{":
            depth += 1
            current.append(ch)
        elif ch in ")]}":
            if depth > 0:
                depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            piece = "".join(current).strip()
            if piece:
                result.append(piece)
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        result.append(tail)
    return result


def parse_window_rule_line(line: str) -> WindowRule | None:
    """Parse a single ``windowrule = …`` line into a :class:`WindowRule`.

    Returns ``None`` for unrelated keywords (including any leftover
    ``windowrulev2 = …`` line that slipped past the migration step)
    or syntactically broken lines (no ``=``, no body, no effect token).

    For multi-effect lines (``windowrule = match:class kitty,
    opacity 0.8, no_blur on``) only the *first* effect is captured;
    callers that need a one-WindowRule-per-effect split should use
    :func:`parse_window_rule_lines` instead.
    """
    head, sep, tail = line.partition("=")
    if not sep:
        return None
    if head.strip() != KEYWORD_WRITE:
        return None
    body = tail.strip()
    if not body:
        return None
    return _parse_v3_body(body)


def _parse_v3_body(body: str) -> WindowRule | None:
    """Parse the body of a v3 ``windowrule = …`` line."""
    tokens = _split_top_level(body)
    if not tokens:
        return None

    matchers: list[Matcher] = []
    effects: list[tuple[str, str]] = []  # (name, args)
    for tok in tokens:
        m = _parse_match_token(tok)
        if m is not None:
            matchers.append(m)
            continue
        # Effect token: first space-separated word is the name, rest is args.
        name, _, args = tok.partition(" ")
        name = name.strip()
        if not name:
            continue
        effects.append((name, args.strip()))

    if not effects:
        # A windowrule without an effect is meaningless; the wiki
        # requires both at least one prop and one effect.
        return None

    # Multiple effects in one rule are valid Hyprland syntax but our
    # data model is one-effect-per-rule. Take the FIRST effect; the
    # extras are dropped here, but :func:`parse_window_rule_lines`
    # below splits them into separate rules so they all survive a
    # round-trip.
    effect_name, effect_args = effects[0]
    return WindowRule(
        matchers=matchers,
        effect_name=effect_name,
        effect_args=effect_args,
    )


def parse_window_rule_lines(lines: list[str]) -> list[WindowRule]:
    """Parse multiple raw rule lines, dropping anything unparseable.

    Multi-effect v3 rules (``windowrule = match:class kitty, opacity
    0.8, no_blur on``) get split into N one-effect rules sharing the
    same matchers — that's how hyprmod's data model represents them,
    and Hyprland treats N rules with identical matchers identically.
    """
    result: list[WindowRule] = []
    for raw in lines:
        result.extend(_parse_with_effect_split(raw))
    return result


def _parse_with_effect_split(line: str) -> list[WindowRule]:
    """Parse one line, splitting multi-effect v3 rules into separate rules."""
    head, sep, tail = line.partition("=")
    if not sep:
        return []
    if head.strip() != KEYWORD_WRITE:
        return []
    body = tail.strip()
    if not body:
        return []

    tokens = _split_top_level(body)
    matchers: list[Matcher] = []
    effects: list[tuple[str, str]] = []
    for tok in tokens:
        m = _parse_match_token(tok)
        if m is not None:
            matchers.append(m)
            continue
        name, _, args = tok.partition(" ")
        name = name.strip()
        if name:
            effects.append((name, args.strip()))

    if not effects:
        return []

    return [
        WindowRule(
            matchers=list(matchers),
            effect_name=name,
            effect_args=args,
        )
        for name, args in effects
    ]


def serialize(items: list[WindowRule]) -> list[str]:
    """Serialize a list of :class:`WindowRule` back to v3 config lines."""
    return [item.to_line() for item in items]
