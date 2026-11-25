"""Pure parsing of visible assistant text and tool-call payloads.

Text cleanup (leaked EOS / chat-template tokens, XML tool dumps),
Qwen/Hermes/GLM XML and JSON tool-call extraction, streamed
tool-call accumulation, and the reasoning/thinking replay shape.
No I/O, no state, no provider calls."""

from __future__ import annotations

import bisect
import json
import math
import re


_ARG_LIMIT_FILE = 400_000
_ARG_LIMIT_ARTIFACT = 400_000
_ARG_OVERFLOW = "[Fuse: tool arguments exceeded the 400000-character limit; call not executed]"


_SPECIAL_TOKENS = re.compile(
    r"<\|(?:eos|endoftext|end|eot_id|im_end|im_start|end_of_text)\|?>"
    r"|</s>",
    re.I,
)
_SPECIAL_TOKEN_MAX = len("<|end_of_text|>")  # the longest text it matches
# A parameter value may quote a closing tag (an edit to this file, echo
# '</function>'), so each element ends at the first closing tag that sits on a
# structural boundary, without crossing the next opener of its kind. Only when
# there is none does the first closing tag end it.
_TOOL_XML_BLOCK = re.compile(
    r"<tool_calls?\b[^>]*>"
    r"(?:(?:(?!<tool_calls?\b).)*?(?:</function>|</invoke>)\s*|(?:(?!<function\s*=|<invoke\b).)*?)"
    r"</tool_calls?>",
    re.I | re.S,
)
_TOOL_XML_OPEN = re.compile(r"<tool_calls?\b", re.I)
_TOOL_XML_CLOSE = re.compile(r"</tool_calls?>", re.I)
_TOOL_XML_CALL_END = re.compile(r"(?:</function>|</invoke>)\s*</tool_calls?>", re.I)
_TOOL_XML_FN = re.compile(r"<function\s*=|<invoke\b", re.I)
_GT = re.compile(">")
_FUNCTION_XML = re.compile(
    r"<function\s*=\s*([A-Za-z][\w.-]*)>((?:(?!<function\s*=).)*?</parameter>\s*(?=</function>)|.*?)</function>",
    re.I | re.S,
)
_INVOKE_XML = re.compile(
    r"<invoke\s+name\s*=\s*[\"']([^\"']+)[\"']>((?:(?!<invoke\b).)*?</parameter>\s*(?=</invoke>)|.*?)</invoke>",
    re.I | re.S,
)
_FUNCTION_CLOSE = re.compile(r"</function>", re.I)
_INVOKE_CLOSE = re.compile(r"</invoke>", re.I)
_PARAM_CLOSE = re.compile(r"</parameter>", re.I)
_PARAM_END = r"(?=</parameter>\s*(?:<parameter\b|</function>|</invoke>|\Z))"
_PARAM_EQ_XML = re.compile(
    r"<parameter\s*=\s*([A-Za-z][\w.-]*)>((?:(?!<parameter\b).)*?" + _PARAM_END + r"|.*?)</parameter>",
    re.I | re.S,
)
_PARAM_NAME_XML = re.compile(
    r"<parameter\s+name\s*=\s*[\"']([^\"']+)[\"']>((?:(?!<parameter\b).)*?" + _PARAM_END + r"|.*?)</parameter>",
    re.I | re.S,
)


class _Next:
    """Where `pattern` next matches in `text` at or after a position. Lookups
    at growing positions reuse the last answer, so asking once per candidate
    scans the text once instead of once per candidate."""

    def __init__(self, text: str, pattern: re.Pattern):
        self.text, self.pattern = text, pattern
        self.since: int | None = None
        self.found: re.Match | None = None

    def __call__(self, pos: int) -> re.Match | None:
        if self.since is not None and self.since <= pos and (self.found is None or self.found.start() >= pos):
            return self.found
        self.found = self.pattern.search(self.text, pos)
        self.since = pos
        return self.found


def _tool_xml_blocks(text: str, start: int = 0):
    """(start, end) of each match of _TOOL_XML_BLOCK.finditer(text, start),
    in linear time. The regex retries from every opener and, for a call that
    never closes, rescans to the end each time: quadratic in the openers."""
    gt, inner, call_end, close, fn = (
        _Next(text, pattern) for pattern in (_GT, _TOOL_XML_OPEN, _TOOL_XML_CALL_END, _TOOL_XML_CLOSE, _TOOL_XML_FN)
    )
    pos = start
    while opener := _TOOL_XML_OPEN.search(text, pos):
        head = gt(opener.end())
        if not head:
            return
        body = head.end()
        # A body that ends in </function> or </invoke> must not cross another
        # opener; one that does not must hold no <function=/<invoke at all.
        end = call_end(body)
        nested = inner(body)
        if not end or (nested and nested.start() < end.start()):
            end = close(body)
            named = fn(body)
            if end and named and named.start() < end.start():
                end = None
        if end:
            yield opener.start(), end.end()
            pos = end.end()
        else:
            pos = opener.start() + 1


_HELD_TOKEN = re.compile(r"(</tool_calls?>)|(<tool_calls?\b)|(<function\s*=|<invoke\b)|>", re.I)


class HeldToolXml:
    """Whether text appended to a held tool call (drop_tool_xml's tail) may
    have completed a _TOOL_XML_BLOCK in it, in time linear in what was
    appended.

    A closing tag completes a block when no <function= or <invoke lies in the
    body of the last opener whose body started before it, or when it follows
    </function> or </invoke> after the last opener's body. An answer of False
    is exact. A token split across calls is found on the next one (a short
    overlap is rescanned), except a <function= with more whitespace before its
    "=" than the overlap holds; missing that only answers True too often."""

    _OVERLAP = 16

    def __init__(self):
        self.scanned = 0
        self.opener = -1  # start of the last opener
        self.body = -1  # body start of the last opener whose ">" has arrived
        self.opener_body = False  # whether that is the last opener
        self.named = -1  # start of the last <function= / <invoke

    def closes(self, held: str) -> bool:
        closed = False
        for token in _HELD_TOKEN.finditer(held, self.scanned):
            at = token.start()
            self.scanned = token.end()
            if token.group(2):
                self.opener, self.opener_body = at, False
                continue
            if token.group(3):
                self.named = at
                continue
            if token.group(1) and self.body >= 0:
                end = at
                while end > self.body and held[end - 1].isspace():
                    end -= 1
                call_end = next((end - len(tag) for tag in ("</function>", "</invoke>")
                                 if held[end - len(tag):end].lower() == tag), -1)
                if self.named < self.body or (self.opener_body and call_end >= self.body):
                    closed = True
            if self.opener >= 0 and not self.opener_body:
                self.body, self.opener_body = token.end(), True
        self.scanned = max(self.scanned, len(held) - self._OVERLAP)
        return closed


def _quoted_in(spans: list[tuple[int, int]]):
    """index -> whether it falls in one of spans (sorted, disjoint)."""
    starts = [lo for lo, _ in spans]

    def quoted(index: int) -> bool:
        at = bisect.bisect_right(starts, index) - 1
        return at >= 0 and index < spans[at][1]

    return quoted


_WS = re.compile(r"\s*")


def _bounded_finditer(pattern: re.Pattern, closer: re.Pattern, text: str, *, lookahead: int = 0):
    """pattern.finditer(text) for a pattern whose every match ends with
    closer, stopped after the last closer (plus whitespace and `lookahead`
    characters a lookahead there reads): an opener past it cannot match, and
    each such opener would otherwise rescan to the end of the text."""
    last = None
    for last in closer.finditer(text):
        pass
    if last is None:
        return iter(())
    endpos = last.end()
    if lookahead:
        endpos = min(len(text), _WS.match(text, endpos).end() + lookahead)
    return pattern.finditer(text, 0, endpos)


def _sub_spans(text: str, spans, repl) -> str:
    parts, pos = [], 0
    for lo, hi in spans:
        parts.append(text[pos:lo])
        parts.append(repl(lo, hi))
        pos = hi
    parts.append(text[pos:])
    return "".join(parts)


def strip_special_tokens(text: str, clean: int = 0) -> str:
    """Remove leaked EOS / chat-template tokens from visible model text.
    text[:clean] is known to hold no whole token (special_token_free) and is
    not scanned again: a held tail restripped on every chunk was quadratic."""
    if not text:
        return text
    cut = max(0, clean - _SPECIAL_TOKEN_MAX)
    if not cut:
        return _SPECIAL_TOKENS.sub("", text)
    return text[:cut] + _SPECIAL_TOKENS.sub("", text[cut:])


def special_token_free(stripped: str, clean: int = 0) -> int:
    """How much of strip_special_tokens(text, clean) holds no whole token
    (removing one can join the text around it into another)."""
    match = _SPECIAL_TOKENS.search(stripped, max(0, clean - 2 * _SPECIAL_TOKEN_MAX))
    return match.start() if match else len(stripped)


def drop_tool_xml(text: str, start: int = 0, code: MdCode | None = None) -> tuple[str, str]:
    """Split assistant text into (visible, incomplete <tool_call> tail).

    Complete XML tool dumps are dropped so they are not shown as the answer.
    An unclosed opener is held back until the next chunk or round end.
    Markdown code is quoted text that extract_text_tool_calls never runs, so a
    call shown there stays visible. Only text[start:] is rewritten; the settled
    prefix is read to tell whether a later opener sits inside code (through
    `code` when given, so a streamed reply is not rescanned on every chunk).
    """
    if not text:
        return "", ""
    if not _TOOL_XML_OPEN.search(text, start):
        return text, ""
    if code is None:
        quoted = _quoted_in([match.span() for match in _MD_CODE.finditer(text)])
    else:
        def quoted(at: int) -> bool:
            return code.quoted(text, at) is True

    parts, pos = [text[:start]], start
    for lo, hi in _tool_xml_blocks(text, start):
        if not quoted(lo):
            parts.append(text[pos:lo])
            pos = hi
    # Held from the first unclosed opener: extract_text_tool_calls parses from
    # there, so text after it is part of that call.
    first = next((match for match in _TOOL_XML_OPEN.finditer(text, pos) if not quoted(match.start())), None)
    if first:
        parts.append(text[pos:first.start()])
        return "".join(parts), text[first.start():]
    parts.append(text[pos:])
    return "".join(parts), ""


_TOOL_XML_NAME = re.compile(
    r"<function\s*=\s*([A-Za-z][\w.-]*)|<invoke\s+name\s*=\s*[\"']([^\"']+)|\"name\"\s*:\s*\"([^\"]+)\"",
    re.I,
)


def tool_xml_start(text: str, start: int = 0) -> int:
    """Offset of the first <tool_call> opener at or after start, or -1."""
    match = _TOOL_XML_OPEN.search(text or "", max(0, start))
    return match.start() if match else -1


def tool_xml_name(text: str, at: int) -> str:
    """Tool name written so far in the XML call opened at `at`, or ""."""
    match = _TOOL_XML_NAME.search((text or "")[at : at + 600])
    return next((group for group in match.groups() if group), "")[:64] if match else ""


def _xml_param_value(raw: str):
    text = (raw or "").strip()
    quoted = len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'"
    if quoted:
        text = text[1:-1]
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
        return text
    # A quoted scalar is a string: "123" must not become 123 (or true/null).
    return text if quoted and not isinstance(value, (dict, list)) else value


def _xml_text(raw: str) -> str:
    """A string parameter as written. Only the newline after the opening tag
    and the one before the closing tag are layout (as Qwen's parser reads
    them); a bare JSON string literal ("README.md") is unquoted."""
    text = raw or ""
    text = text[1:] if text.startswith("\n") else text
    text = text[:-1] if text.endswith("\n") else text
    if text.startswith('"'):
        try:
            value = json.loads(text)
        except (json.JSONDecodeError, ValueError, RecursionError):
            return text
        return value if isinstance(value, str) else text
    return text


def _xml_params(body: str) -> dict:
    """Raw parameter text; _xml_call types it against the tool's schema."""
    args = {}
    # _PARAM_END reads past a closing tag: \s* then up to 11 characters.
    for match in _bounded_finditer(_PARAM_EQ_XML, _PARAM_CLOSE, body or "", lookahead=12):
        args[match.group(1)] = match.group(2)
    for match in _bounded_finditer(_PARAM_NAME_XML, _PARAM_CLOSE, body or "", lookahead=12):
        args[match.group(1)] = match.group(2)
    return args


def _string_params(tool: str) -> set[str]:
    from ..chat_tools import _schema_for

    schema = _schema_for(tool) or {}
    props = ((schema.get("function") or {}).get("parameters") or {}).get("properties") or {}
    return {key for key, spec in props.items() if isinstance(spec, dict) and spec.get("type") == "string"}


def _xml_call(name: str, args: dict, index: int, *, coding: bool, raw: bool = False) -> dict | None:
    from ..chat_tools import suggest_tool

    resolved = suggest_tool(name, json.dumps(args, ensure_ascii=False), coding=coding) or (name or "").strip()
    if not resolved:
        return None
    if raw:
        # A string parameter is text even when it reads as JSON or a number
        # (write_file content '{"a": 1}', '42'); the rest are typed values.
        strings = _string_params(resolved)
        args = {key: _xml_text(value) if key in strings else _xml_param_value(value) for key, value in args.items()}
    return {
        "id": f"xml_{index}",
        "type": "function",
        "function": {
            "name": resolved[:64],
            "arguments": _bounded_arguments(json.dumps(args, ensure_ascii=False)),
        },
    }


def _calls_from_xml_body(body: str, *, coding: bool, start: int = 0) -> list[dict]:
    calls = []
    for match in _bounded_finditer(_FUNCTION_XML, _FUNCTION_CLOSE, body or ""):
        rec = _xml_call(match.group(1), _xml_params(match.group(2)), start + len(calls), coding=coding, raw=True)
        if rec:
            calls.append(rec)
    if calls:
        return calls
    for match in _bounded_finditer(_INVOKE_XML, _INVOKE_CLOSE, body or ""):
        rec = _xml_call(match.group(1), _xml_params(match.group(2)), start + len(calls), coding=coding, raw=True)
        if rec:
            calls.append(rec)
    if calls:
        return calls
    blob = re.sub(
        r"^<tool_calls?\b[^>]*>|</tool_calls?>$",
        "",
        (body or "").strip(),
        flags=re.I,
    ).strip()
    if not blob:
        return calls
    try:
        parsed = json.loads(blob)
    except (json.JSONDecodeError, ValueError, RecursionError):
        parsed = None
    items = parsed if isinstance(parsed, list) else [parsed] if isinstance(parsed, dict) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        fn = item.get("function") if isinstance(item.get("function"), dict) else {}
        name = item.get("name") or fn.get("name")
        args = (item.get("arguments") if "arguments" in item
                else item.get("parameters") or item.get("input") or fn.get("arguments") or {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, ValueError, RecursionError):
                args = {"_": args}
        if not isinstance(args, dict):
            args = {"value": args}
        rec = _xml_call(str(name or ""), args, start + len(calls), coding=coding)
        if rec:
            calls.append(rec)
    return calls


# Markdown code (fenced blocks, inline spans) is quoted text: a tool call shown
# as an example there must never execute.
# A closing fence is a line with at least as many of the opening character.
_MD_CODE = re.compile(
    r"^[ \t]{0,3}(`{3,})(?!`)[^\n]*(?:\n|\Z)[\s\S]*?(?:^[ \t]{0,3}\1`*[ \t]*(?=\n|$)|\Z)"
    r"|^[ \t]{0,3}(~{3,})(?!~)[^\n]*(?:\n|\Z)[\s\S]*?(?:^[ \t]{0,3}\2~*[ \t]*(?=\n|$)|\Z)"
    r"|(?<!`)(`+)(?!`)[^\n]+?(?<!`)\3(?!`)",
    re.M,
)


def in_md_code(text: str, at: int) -> bool | None:
    """Whether text[at] sits in markdown code, as _MD_CODE reads it. None
    while text is still streaming and cannot tell yet: an inline span opened
    before it on its line may close later on that line."""
    spans = [match.span() for match in _MD_CODE.finditer(text)]
    if any(lo <= at < hi for lo, hi in spans):
        return True
    if "\n" in text[at:]:
        return False
    line = max([text.rfind("\n", 0, at) + 1] + [hi for _, hi in spans if hi <= at])
    return None if "`" in text[line:at] else False


_TICK_RUN = re.compile(r"`+")
_FENCE_OPEN = re.compile(r"[ \t]{0,3}(`{3,}(?!`)|~{3,}(?!~))")
_FENCE_UNSURE = re.compile(r"[ \t]{0,3}(?:`{0,2}|~{0,2})")
_FENCE_PROBE = re.compile(r"[ \t]{0,3}(?:`+|~+)?")
_FENCE_CLOSE = re.compile(r"[ \t]{0,3}(`+|~+)[ \t]*")


class MdCode:
    """in_md_code and drop_tool_xml's code lookups for a streamed reply, in
    linear time. Asking after every chunk with _MD_CODE.finditer over the
    whole text was quadratic in its length (thousands of </think> tags).

    Each line is scanned once, as _MD_CODE reads it: a fence runs from its
    opener line to a closing line; on other lines a backtick run opens a span
    that the first later run of the same length on that line closes. Runs
    still unclosed (`opens`) are what text arriving later can change. Text
    changed before what was read (a dropped tag or tool call) is rescanned
    from the last mark before the change."""

    def __init__(self):
        self.text = ""
        self.spans: list[tuple[int, int]] = []
        # (pos, line start, spans kept, fence, line classified) where no
        # backtick run is open: rescans restart from one of these.
        self._marks = [(0, 0, 0, None, False)]
        self._look = None
        self._restore(0)

    def _restore(self, index: int) -> None:
        del self._marks[index + 1:]
        self.cur, self.ls, kept, self.fence, self.known = self._marks[index]
        del self.spans[kept:]
        self.opener = False  # on the fence's opener line, which never closes it
        self.probe = self.cur  # the line's fence check read text before this
        self.opens: list[tuple[int, int]] = []
        self.open_len: dict[int, int] = {}

    def _mark(self) -> None:
        if self._marks[-1][0] < self.cur:
            self._marks.append((self.cur, self.ls, len(self.spans), self.fence, self.known))

    def _read(self) -> int:
        """How much of the text what is settled depends on."""
        return max(self.cur, self.probe) if self.known else self.cur

    def forget(self, at: int) -> None:
        """Text from at on may change: rescan it on the next lookup."""
        if at < self._read():
            self._restore(bisect.bisect_right(self._marks, at, key=lambda mark: mark[0]) - 1)

    def _sync(self, text: str) -> None:
        if text is not self.text:
            read = self._read()
            if len(text) < read or text[:read] != self.text[:read]:
                self._restore(0)
            self.text = text

    def _scan(self, bound: int) -> None:
        text = self.text
        while self.cur < bound:
            if self.fence is not None:
                nl = text.find("\n", self.cur, bound)
                if nl < 0:
                    self.cur = bound
                    return
                start, char, size = self.fence
                close = None if self.opener else _FENCE_CLOSE.fullmatch(text, self.ls, nl)
                if close and close[1][0] == char and len(close[1]) >= size:
                    self.spans.append((start, nl))
                    self.fence = None
                self.opener = self.known = False
                self.ls = self.cur = nl + 1
                self._mark()
                continue
            nl = text.find("\n", self.cur, bound)
            end = nl if nl >= 0 else bound
            if not self.known:
                fence = _FENCE_OPEN.match(text, self.ls, end)
                if fence and (nl >= 0 or fence.end() < end):
                    self.fence = (self.ls, fence[1][0], len(fence[1]))
                    self.opener = True
                    self.cur = fence.end()
                    continue
                if nl < 0 and (fence or _FENCE_UNSURE.fullmatch(text, self.ls, end)):
                    return  # the line may still open a fence
                self.known = True
                self.probe = _FENCE_PROBE.match(text, self.ls, end).end() + 1
            for run in _TICK_RUN.finditer(text, self.cur, end):
                lo, hi = run.span()
                if hi == end and nl < 0:
                    self.cur = lo  # the run may still grow
                    return
                if not self.opens and lo >= self.probe:
                    self.cur = lo
                    self._mark()
                start = self.open_len.get(hi - lo)
                if start is None:
                    self.opens.append((lo, hi - lo))
                    self.open_len[hi - lo] = lo
                    continue
                while True:
                    pos, size = self.opens.pop()
                    del self.open_len[size]
                    if pos == start:
                        break
                while self.spans and self.spans[-1][0] > start:
                    self.spans.pop()
                self.spans.append((start, hi))
            if nl < 0:
                self.cur = bound
                if not self.opens and self.cur >= self.probe:
                    self._mark()
                return
            self.opens.clear()
            self.open_len.clear()
            self.known = False
            self.ls = self.cur = nl + 1
            self._mark()

    def quoted(self, text: str, at: int) -> bool | None:
        """in_md_code(text, at), where text mostly grows between calls."""
        if at >= len(text) or text[at] in "`\n":
            return in_md_code(text, at)
        self._sync(text)
        self.forget(at)
        self._scan(at)
        if self.fence is not None or (not self.known and _FENCE_OPEN.match(text, self.ls)):
            return True
        # Unclosed runs before at (and the run ending at at, settled only
        # now that at is known not to be a backtick) quote it if a later run
        # on its line has the same length.
        sizes = [size for _, size in self.opens]
        line = max(self.ls, self.spans[-1][1] if self.spans else 0)
        tick = bool(self.opens) and self.opens[-1][0] >= line
        run = _TICK_RUN.search(text, self.cur, at)
        if run:
            start = self.open_len.get(run.end() - run.start())
            if start is None:
                sizes.append(run.end() - run.start())
                tick = True
            else:
                sizes = [size for pos, size in self.opens if pos < start]
                tick = False
        if not sizes:
            return False
        closed, newline = self._ahead(text, at, sizes)
        if closed:
            return True
        return None if tick and not newline else False

    def _ahead(self, text: str, at: int, sizes: list[int]) -> tuple[bool, bool]:
        """Whether a backtick run after at on its line has one of sizes, and
        whether that line ends in text. The runs found are kept: the next
        lookup is usually the same line grown at its end, or with a tag
        before them dropped."""
        look = self._look
        if look is not None:
            old, base, end, newline, last, shift, tail = look
            stop = end + newline
            # Unchanged where it was scanned, or shifted by text dropped before at.
            for moved in (0, len(text) - len(old)):
                if base <= at - moved <= end and (
                    text is old and not moved or text[at:stop + moved] == old[at - moved:stop]
                ):
                    end += moved
                    shift += moved
                    break
            else:
                look = None
        if look is None:
            end, newline, last, shift, tail = at, False, {}, 0, None
        if not newline:
            if tail is not None:
                end, tail = tail + shift, None
            nl = text.find("\n", end)
            stop = nl if nl >= 0 else len(text)
            for run in _TICK_RUN.finditer(text, end, stop):
                if nl < 0 and run.end() == stop:
                    tail = run.start() - shift
                else:
                    last[run.end() - run.start()] = run.start() - shift
            end, newline = stop, nl >= 0
        self._look = (text, at, end, newline, last, shift, tail)
        tail_size = len(text) - tail - shift if tail is not None else 0
        after = at - shift
        closed = any(size == tail_size or last.get(size, after) > after for size in sizes)
        return closed, newline


def extract_text_tool_calls(content: str, *, coding: bool = False) -> tuple[str, list[dict]]:
    """Parse Qwen/Hermes/GLM XML or JSON tool dumps out of assistant content."""
    if not content:
        return content, []
    lowered = content.lower()
    if "<tool" not in lowered and "<function" not in lowered and "<invoke" not in lowered:
        return content, []
    quoted = _quoted_in([match.span() for match in _MD_CODE.finditer(content)])

    calls: list[dict] = []

    def _replace(lo: int, hi: int) -> str:
        block = content[lo:hi]
        if quoted(lo):
            return block
        extracted = _calls_from_xml_body(block, coding=coding, start=len(calls))
        if not extracted:
            # Inner body without the outer tags, in case the regex included them.
            inner = re.sub(r"^</?tool_calls?\b[^>]*>|</tool_calls?>$", "", block, flags=re.I | re.S)
            extracted = _calls_from_xml_body(inner, coding=coding, start=len(calls))
        if extracted:
            calls.extend(extracted)
            return ""
        return block

    def _spans(matches):
        return (match.span() for match in matches)

    cleaned = _sub_spans(content, _tool_xml_blocks(content), _replace)
    if not calls:
        opener = next((m for m in _TOOL_XML_OPEN.finditer(content) if not quoted(m.start())), None)
        if opener:
            extracted = _calls_from_xml_body(content[opener.end() :], coding=coding)
            if extracted:
                calls.extend(extracted)
                cleaned = cleaned[: opener.start()]
    if not calls:
        cleaned = _sub_spans(content, _spans(_bounded_finditer(_FUNCTION_XML, _FUNCTION_CLOSE, content)), _replace)
    if not calls:
        cleaned = _sub_spans(content, _spans(_bounded_finditer(_INVOKE_XML, _INVOKE_CLOSE, content)), _replace)
    return cleaned.strip(), calls


def _finalize_assistant_payload(content, reasoning, calls, finish, *, coding: bool = False):
    content = content or ""
    reasoning = strip_special_tokens(reasoning or "")
    allc = [c for c in (calls or []) if isinstance(c, dict)]
    named = [c for c in allc if (c.get("function") or {}).get("name")]
    # Nameless entries ride along so the round loop can log them as
    # malformed calls; execution filters them again before tools run.
    native = allc
    content, extracted = extract_text_tool_calls(content, coding=coding)
    content = strip_special_tokens(content)
    if not named and extracted:
        # Keep the nameless entries too: the round loop logs them as
        # malformed calls; the extracted XML calls carry the real work.
        native = [*allc, *extracted]
    if named or extracted:
        finish = "tool_calls"
    return content, reasoning, native, finish


def calls_incomplete(calls: list) -> bool:
    """True when any tool call has truncated JSON arguments.

    Empty arguments are a complete no-argument call (some OpenAI-compatible
    providers send "" instead of "{}"); the tool runner executes them as {}.
    The oversize marker is not a truncation either: the runner refuses it
    with its own corrective message, and asking for a full re-issue would
    only reproduce the same oversized call.
    """
    for call in calls or []:
        if not isinstance(call, dict):
            return True
        fn = call.get("function") if isinstance(call.get("function"), dict) else {}
        if not fn.get("name"):
            return True
        raw = fn.get("arguments")
        if raw is None or (isinstance(raw, str) and (not raw.strip() or raw == _ARG_OVERFLOW)):
            continue
        if isinstance(raw, str):
            try:
                json.loads(raw)
            except (json.JSONDecodeError, ValueError, RecursionError):
                return True
    return False


def _canonical_arguments(raw: str) -> str:
    """Arguments as sorted compact JSON, so spacing or key order alone never
    makes a repeated call look new; unparsable ones compare as written."""
    try:
        return json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError, RecursionError):
        return raw


def round_signature(calls: list) -> tuple:
    sig = []
    for call in calls or []:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") if isinstance(call.get("function"), dict) else {}
        sig.append((str(fn.get("name") or ""), _canonical_arguments(str(fn.get("arguments") or ""))))
    return tuple(sorted(sig))


def _complete_json_object(raw: str):
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _bounded_arguments(raw: str) -> str:
    # Never execute a clipped prefix, even when that prefix happens to be JSON.
    return raw if len(raw) <= _ARG_LIMIT_FILE else _ARG_OVERFLOW


def _take_calls(bucket: dict, raw) -> None:
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index", len(bucket)))
        except (TypeError, ValueError, OverflowError):
            idx = len(bucket)
        fn = item.get("function") if isinstance(item.get("function"), dict) else {}
        name = fn.get("name")
        # Providers that reuse one index for parallel calls tell them apart
        # only by id: a named call with a new id opens a record filed after
        # the index's newest one, and the fragments that follow land there.
        siblings = [key for key in bucket if math.floor(key) == idx]
        key = max(siblings, default=idx)
        held = bucket.get(key)
        item_id = str(item.get("id") or "")[:128]
        if held and item_id and held["id"] and item_id != held["id"] and held["function"]["name"] and name:
            key = idx + len(siblings) / (len(siblings) + 1)
        rec = bucket.setdefault(
            key,
            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
        )
        if item_id:
            rec["id"] = item_id
        if isinstance(name, str) and name:
            cur = rec["function"]["name"]
            if not cur or name.startswith(cur):
                rec["function"]["name"] = name[:64]
            else:
                rec["function"]["name"] = (cur + name)[:64]
        args = fn.get("arguments")
        if rec["function"]["arguments"] == _ARG_OVERFLOW:
            continue
        if isinstance(args, dict):
            rec["function"]["arguments"] = _bounded_arguments(json.dumps(args, ensure_ascii=False))
        elif isinstance(args, str) and args:
            held_args = rec["function"]["arguments"]
            # A named delta may resend the whole arguments so far; one that only
            # carries the next fragment ('{}' after '{"a": ') still appends.
            if fn.get("name") and (not held_args or args.startswith(held_args)):
                parsed = _complete_json_object(args)
                if parsed is not None:
                    rec["function"]["arguments"] = _bounded_arguments(args)
                    continue
            rec["function"]["arguments"] = _bounded_arguments(rec["function"]["arguments"] + args)


def _claude_position(block: dict) -> dict:
    """Where a Claude thinking block sat among the text and tool calls
    (claude_stream), so the payload can replay the turn in received order."""
    return {key: block[key] for key in ("tool_index", "after_text") if key in block}


def _take_thinking(bucket: list, raw) -> None:
    if not isinstance(raw, list):
        return
    for block in raw:
        if not isinstance(block, dict):
            continue
        redacted = str(block.get("redacted_thinking") or "")
        if redacted:
            bucket.append({"redacted_thinking": redacted, **_claude_position(block)})
            continue
        if block.get("devin_signature"):
            # Devin signs the turn's whole reasoning; the text streams apart.
            bucket.append({"devin_signature": str(block["devin_signature"]),
                           "devin_signature_type": str(block.get("devin_signature_type") or "")})
            continue
        reasoning_item = block.get("openai_reasoning")
        if isinstance(reasoning_item, dict) and reasoning_item.get("encrypted_content"):
            bucket.append({"openai_reasoning": dict(reasoning_item)})
            continue
        text = str(block.get("thinking") or block.get("text") or "")
        signature = str(block.get("signature") or "")
        # Signed-empty is a valid omitted-display block and must replay.
        if signature:
            bucket.append({"thinking": text, "signature": signature, **_claude_position(block)})


def _take_reasoning_details(bucket: list, raw) -> None:
    """OpenRouter reasoning_details, merged as its SDK merges stream deltas:
    consecutive text (or summary) fragments of one block join; every other
    item (encrypted data) is kept whole."""
    if not isinstance(raw, list):
        return
    for detail in raw:
        if not isinstance(detail, dict):
            continue
        field = {"reasoning.text": "text", "reasoning.summary": "summary"}.get(detail.get("type"))
        last = bucket[-1] if bucket else None
        if field and last and last.get("type") == detail.get("type") and last.get("index") == detail.get("index"):
            last[field] = str(last.get(field) or "") + str(detail.get(field) or "")
            for key in ("signature", "format"):
                if not last.get(key) and detail.get(key):
                    last[key] = detail[key]
        else:
            bucket.append(dict(detail))


# Models whose chat API takes their reasoning back as reasoning_content: Kimi
# K3.x, MiMo and DeepSeek thinking mode require it on tool-call turns, GLM
# keeps preserved thinking. Aggregator routes count when they serve the same
# model. Grok is absent: its Chat Completions reasoning_content is a summary
# with no request field (the ciphertext exists only on the Responses API).
_REPLAYS_REASONING = (
    "glm-", "kimi-", "k3-", "k3.", "mimo-",
    "openrouter/z-ai/", "openrouter/moonshotai/", "openrouter/xiaomi/",
    "nanogpt/z-ai/", "nanogpt/moonshotai/", "nanogpt/xiaomi/", "nanogpt/deepseek/", "nanogpt/minimax/",
    "refuseless/glm-", "refuseless/deepseek-", "refuseless/minimax-", "refuseless/kimi-", "refuseless/mimo-",
)
# Claude and Gemini text blocks cannot be replayed without their signature (a
# stream cut before it arrived); OpenRouter's SDK drops them the same way.
_SIGNED_FORMATS = ("anthropic-claude-v1", "google-gemini-v1")


def _native_reasoning(reasoning, model, thinking=None, source_type=None):
    """Keep only the reasoning the serving provider will be sent again.

    Preserve native reasoning for backends whose message adapters replay it.
    Claude and Devin retain their signed thinking blocks separately; OpenAI and
    Grok do not replay their visible reasoning text as conversation messages.
    """
    out = {}
    details = next((block["reasoning_details"] for block in thinking or []
                    if isinstance(block, dict) and isinstance(block.get("reasoning_details"), list)), None)
    if details and model.startswith("openrouter/"):
        # Replayed unmodified, with the plain text beside it as OpenRouter's SDK does.
        details = [d for d in details if not (d.get("type") == "reasoning.text" and not d.get("signature")
                                              and d.get("format") in _SIGNED_FORMATS)]
        if details:
            out["reasoning_details"] = details
            if reasoning:
                out["reasoning_content"] = str(reasoning)
            out["_fuse_reasoning_model"] = model
            return out
    # Ollama ids are bare names; its /api/chat takes reasoning back as
    # message.thinking (ollama.to_ollama).
    if model == "k3" or model.startswith(_REPLAYS_REASONING) or source_type == "ollama":
        if reasoning:
            out["reasoning_content"] = str(reasoning)
            out["_fuse_reasoning_model"] = model
        return out
    keeps_blocks = model.startswith("claude/") or model.startswith("claude-")
    keeps_text = model.startswith("devin/")
    if not keeps_blocks and not keeps_text:
        # ChatGPT (Codex Responses, store=false): its encrypted reasoning
        # items are the only way the model keeps its reasoning across rounds.
        items = [dict(block) for block in thinking or []
                 if isinstance(block, dict) and isinstance(block.get("openai_reasoning"), dict)]
        if items:
            out["claude_thinking"] = items
            out["_fuse_reasoning_model"] = model
        return out
    blocks = []
    if keeps_text:
        # Devin streams its reasoning as text and signs it once at the end;
        # replay both as one block so the next request carries what the model
        # produced (devin_payload reads the signature back from it).
        signed = next((block for block in thinking or [] if isinstance(block, dict) and block.get("devin_signature")), None)
        if reasoning or signed:
            block = {"thinking": str(reasoning or ""), "signature": str((signed or {}).get("devin_signature") or "")}
            if (signed or {}).get("devin_signature_type"):
                block["signature_type"] = str(signed["devin_signature_type"])
            out["claude_thinking"] = [block]
            out["_fuse_reasoning_model"] = model
        return out
    for block in thinking or []:
        if not isinstance(block, dict):
            continue
        redacted = str(block.get("redacted_thinking") or "")
        if redacted:
            blocks.append({"redacted_thinking": redacted, **_claude_position(block)})
            continue
        text = str(block.get("thinking") or block.get("text") or "")
        signature = str(block.get("signature") or "")
        if (text or signature) and (signature or keeps_text):
            blocks.append({"thinking": text, "signature": signature, **_claude_position(block)})
    if blocks:
        out["claude_thinking"] = blocks
        out["_fuse_reasoning_model"] = model
    return out
