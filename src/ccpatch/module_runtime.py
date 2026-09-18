"""Dependency-free bootstrap plumbing for split Bun ESM graphs."""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from itertools import pairwise

_MARKER = re.compile(r"\n/\* ccpatch-module:([0-9a-f]+) \*/\n")
_BOOTSTRAP = re.compile(r"/\* ccpatch-bootstrap:([0-9a-f]+(?: [0-9a-f]+)*) \*/")
_ID = r"[A-Za-z_$][\w$]*"
_COMMENT = r"/\*[\s\S]*?\*/|//[^\r\n]*"
_GAP = rf"(?:\s|{_COMMENT})*"
_IMPORT = re.compile(
    rf'import\b{_GAP}\{{([^{{}}]*)\}}{_GAP}from\b{_GAP}[\"\']([^\"\']+)[\"\']'
)
_EXPORT = re.compile(rf'export\b{_GAP}\{{([^{{}}]*)\}}(?!{_GAP}from\b)')


class ModuleRuntimeError(RuntimeError):
    """A module bridge cannot preserve the native graph semantics."""


@dataclass(frozen=True)
class SourceModule:
    name: str
    start: int
    end: int
    source: str


def source_modules(source: str) -> tuple[SourceModule, ...]:
    """Return module bodies and their aggregate-source offsets."""
    matches = list(_MARKER.finditer(source))
    if not matches:
        return (SourceModule("", 0, len(source), source),)
    if matches[0].start() != 0:
        raise ModuleRuntimeError("text before the first module boundary")
    ends = [match.start() for match in matches[1:]] + [len(source)]
    return tuple(
        SourceModule(
            bytes.fromhex(match[1]).decode(),
            match.end(),
            end,
            source[match.end() : end],
        )
        for match, end in zip(matches, ends, strict=True)
    )


def _module_at(source: str, offset: int) -> SourceModule:
    for module in source_modules(source):
        if module.start <= offset < module.end:
            return module
    raise ModuleRuntimeError(f"offset {offset} is outside module bodies")


_TOKEN = re.compile(
    r'(?P<space>\s+)|(?P<comment>//[^\n\r]*|/\*[\s\S]*?\*/)'
    r'|(?P<string>"(?:\\[\s\S]|[^"\\])*"|\'(?:\\[\s\S]|[^\'\\])*\')'
    r'|(?P<word>[\w$]+)|(?P<operator>=>|\+\+|--|\?\.|[^\w\s])'
)
_REGEX = re.compile(
    r'/ (?: \\[\s\S] | \[(?:\\[\s\S]|[^\]\\\r\n])*\] | [^/\\[\r\n] )* / [a-z]*', re.X
)
_TEMPLATE = re.compile(r'\\[\s\S]|\$\{|`|[^\\`$]+|\$')


@lru_cache(maxsize=32)
def _declarations(source: str) -> tuple[tuple[str, int], ...]:
    """Locate top-level declaration tokens, not text in JavaScript literals."""
    result: list[tuple[str, int]] = []
    # Each delimiter retains the lexical goal after its closing token.
    stack: list[tuple[str, bool]] = []
    bodies: list[tuple[int, bool]] = []
    template = False
    expression = True
    previous = ""
    offset = 0
    while offset < len(source):
        if template:
            part = _TEMPLATE.match(source, offset)
            if part is None:
                raise ModuleRuntimeError("unterminated JavaScript template")
            text = part[0]
            offset = part.end()
            if text == "`":
                template = False
                expression = False
            elif text == "${":
                stack.append(("${", False))
                template = False
                expression = True
            continue
        token = _TOKEN.match(source, offset)
        if token is None:
            raise ModuleRuntimeError("unsupported JavaScript token")
        text = token[0]
        start = offset
        offset = token.end()
        if token.lastgroup in {"space", "comment"}:
            continue
        if text == "`":
            template = True
            continue
        if text == "/" and expression:
            regex = _REGEX.match(source, start)
            if regex is None:
                raise ModuleRuntimeError("unsupported JavaScript regular expression")
            offset = regex.end()
            expression = False
            previous = "literal"
            continue
        if not stack and text in {"import", "export"} and previous not in {".", "?."}:
            result.append((text, start))
        if text in {"function", "class"}:
            bodies.append((len(stack), previous in {"", ";", "}", "export", "default"}))
        if text in {"(", "[", "{"}:
            after = False
            if text == "(":
                after = previous in {"if", "while", "for", "with", "switch", "catch"}
            elif text == "{":
                after = previous in {
                    "",
                    ";",
                    "{",
                    "}",
                    ")",
                    "else",
                    "try",
                    "finally",
                    "do",
                }
                if bodies and bodies[-1][0] == len(stack):
                    _, after = bodies.pop()
            stack.append((text, after))
            expression = True
        elif text in {")", "]", "}"}:
            if stack:
                opening, expression = stack.pop()
                if opening == "${":
                    template = True
            else:
                expression = False
        elif token.lastgroup == "string":
            expression = False
        elif token.lastgroup == "word":
            expression = text in {
                "return",
                "throw",
                "case",
                "delete",
                "void",
                "typeof",
                "new",
                "in",
                "instanceof",
                "yield",
                "await",
                "else",
                "do",
            }
        else:
            expression = text not in {".", "?.", "++", "--"}
        previous = text
    return tuple(result)


def _declaration_matches(source: str, kind: str) -> Iterator[re.Match[str]]:
    pattern = _IMPORT if kind == "import" else _EXPORT
    for declaration, offset in _declarations(source):
        if declaration == kind and (match := pattern.match(source, offset)):
            yield match


def _aliases(specifiers: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for item in re.sub(_COMMENT, " ", specifiers).split(","):
        item = item.strip()
        if not item:
            continue
        match = re.fullmatch(rf"({_ID})(?:\s+as\s+({_ID}))?", item)
        if match is None:
            raise ModuleRuntimeError(f"unsupported ESM binding: {item!r}")
        result.append((match[1], match[2] or match[1]))
    return result


def module_reference(
    source: str, definition_offset: int, native_name: str, consumer_offset: int
) -> str:
    """Resolve a native binding through an existing named import.

    Do not add dependency edges. Fail if the consumer does not import the binding.
    The definition offset must point into the module that owns the local binding.
    """
    owner = _module_at(source, definition_offset)
    consumer = _module_at(source, consumer_offset)
    if owner.name == consumer.name:
        return native_name
    exported = {
        public
        for match in _declaration_matches(owner.source, "export")
        for local, public in _aliases(match[1])
        if local == native_name
    }
    candidates: set[str] = set()
    for match in _declaration_matches(consumer.source, "import"):
        target = match[2]
        if target.startswith("."):
            target = posixpath.normpath(
                posixpath.join(posixpath.dirname(consumer.name), target)
            )
        if target != owner.name:
            continue
        candidates.update(
            local for public, local in _aliases(match[1]) if public in exported
        )
    if not candidates:
        modules = source_modules(source)
        imports: dict[str, tuple[re.Match[str], ...]] = {}
        exports: dict[str, tuple[re.Match[str], ...]] = {}
        exported_bindings = {(owner.name, public) for public in exported}
        changed = True
        while changed:
            changed = False
            for module in modules:
                if not any(public in module.source for _, public in exported_bindings):
                    continue
                if module.name not in imports:
                    imports[module.name] = tuple(
                        _declaration_matches(module.source, "import")
                    )
                    exports[module.name] = tuple(
                        _declaration_matches(module.source, "export")
                    )
                imported: set[str] = set()
                for match in imports[module.name]:
                    target = match[2]
                    if target.startswith("."):
                        target = posixpath.normpath(
                            posixpath.join(posixpath.dirname(module.name), target)
                        )
                    imported.update(
                        local
                        for public, local in _aliases(match[1])
                        if (target, public) in exported_bindings
                    )
                if module.name == consumer.name:
                    candidates.update(imported)
                for match in exports[module.name]:
                    for local, public in _aliases(match[1]):
                        binding = (module.name, public)
                        if local in imported and binding not in exported_bindings:
                            exported_bindings.add(binding)
                            changed = True
    if len(candidates) != 1:
        raise ModuleRuntimeError(
            f"no unique import of {owner.name}:{native_name} in {consumer.name}"
        )
    return candidates.pop()


def ensure_module_reference(
    source: str, definition_offset: int, native_name: str, consumer_offset: int
) -> tuple[str, str]:
    """Add a live binding on an existing edge without changing evaluation order."""
    try:
        return source, module_reference(
            source, definition_offset, native_name, consumer_offset
        )
    except ModuleRuntimeError:
        pass
    owner = _module_at(source, definition_offset)
    consumer = _module_at(source, consumer_offset)
    edges = []
    for match in _declaration_matches(consumer.source, "import"):
        target = match[2]
        if target.startswith("."):
            target = posixpath.normpath(
                posixpath.join(posixpath.dirname(consumer.name), target)
            )
        if target == owner.name:
            edges.append(match)
    if not edges:
        modules = {module.name: module for module in source_modules(source)}
        pending = [(consumer.name, [consumer.name])]
        visited = {consumer.name}
        route: list[str] | None = None
        for current, path in pending:
            for match in _declaration_matches(modules[current].source, "import"):
                target = match[2]
                if target.startswith("."):
                    target = posixpath.normpath(
                        posixpath.join(posixpath.dirname(current), target)
                    )
                if target == owner.name:
                    route = [*path, target]
                    break
                if target in modules and target not in visited:
                    visited.add(target)
                    pending.append((target, [*path, target]))
            if route is not None:
                break
        if route is None:
            raise ModuleRuntimeError(
                f"cannot add dependency edge from {consumer.name} to {owner.name}"
            )
        binding = native_name
        for parent, child in reversed(list(pairwise(route))):
            modules = {module.name: module for module in source_modules(source)}
            source, binding = ensure_module_reference(
                source, modules[child].start, binding, modules[parent].start
            )
        return source, binding
    index = 0
    while True:
        alias = f"__ccpatchNativeBinding{index}"
        if alias not in source:
            break
        index += 1
    edge = edges[0]
    insert = consumer.start + edge.start(1)
    edits = [
        (insert, f"{alias} as {alias},"),
        (owner.end, f"\nexport {{{native_name} as {alias}}};\n"),
    ]
    for offset, text in sorted(edits, reverse=True):
        source = source[:offset] + text + source[offset:]
    return source, alias


def register_module_bootstrap(source: str, key: str, code: str) -> str:
    """Register code that returns a singleton without native-module dependencies.

    Consumers use ``globalThis.__ccpatchRuntime[key]``. Code runs in a function
    body with no arguments. It must return the shared state or helper object.
    Registration order defines dependency order between bootstrap components.
    Do not access native module bindings, import modules, or restore worker
    settings here. Capture transport here; restore it at the native claim hook.
    """
    payload = json.dumps([key, code], ensure_ascii=True).encode().hex()
    # Bound word lengths so native identifier patterns cannot rescan the payload.
    payload = " ".join(
        payload[index : index + 64] for index in range(0, len(payload), 64)
    )
    marker = f"/* ccpatch-bootstrap:{payload} */"
    modules = source_modules(source)
    offset = modules[0].start
    return source[:offset] + marker + source[offset:]


def finalize_module_runtime(source: str) -> str:
    """Install all registered singletons before each module's native statements.

    Repeat the guarded bootstrap at each module boundary. ESM dependency and
    cycle evaluation order therefore cannot defer capture until the entrypoint.
    """
    registrations: dict[str, str] = {}
    # Registrations are prepended, so reverse them to retain registration order.
    for match in reversed(list(_BOOTSTRAP.finditer(source))):
        key, code = json.loads(bytes.fromhex(match[1]))
        if not isinstance(key, str) or not isinstance(code, str):
            raise ModuleRuntimeError("invalid bootstrap registration")
        if key in registrations and registrations[key] != code:
            raise ModuleRuntimeError(f"conflicting bootstrap registration: {key}")
        registrations[key] = code
    if not registrations:
        return source
    source = _BOOTSTRAP.sub("", source)
    prelude = 'globalThis.__ccpatchRuntime??=Object.create(null);'
    for key, code in registrations.items():
        quoted = json.dumps(key)
        prelude += (
            f'if(!Object.hasOwn(globalThis.__ccpatchRuntime,{quoted}))'
            f'globalThis.__ccpatchRuntime[{quoted}]=(function(){{{code}\n}})();'
        )
    parts: list[str] = []
    offset = 0
    for module in source_modules(source):
        parts.extend((source[offset : module.start], prelude, "\n", module.source))
        offset = module.end
    return "".join(parts)
