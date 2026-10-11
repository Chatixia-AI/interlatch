"""The dashboard's Japanese UI is complete: every string app.js passes to t(), tn(), tx() or tc() has a translation in
ja.js that keeps its {placeholders}, ja.js holds nothing unused, and keys are plain "..." strings the scan can read."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "src" / "chronicle" / "web"
HELPERS = {"t": (0,), "tx": (0,), "tn": (1, 2), "tc": (0, 1)}  # which arguments are the English text (tc: context, text)
REGEX_BEFORE = set("(,=:[!&|?{};+-*%<>~^")  # a "/" after one of these starts a regex literal, not a division
KEYWORDS_BEFORE_REGEX = {"return", "typeof", "case", "in", "of", "delete", "void", "throw", "new", "else", "do"}
PLAIN = re.compile(r'"(?:[^"\\\n]|\\.)*"')
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _skip_string(src: str, i: int) -> int:
    q, i = src[i], i + 1
    while src[i] != q:
        i += 2 if src[i] == "\\" else 1
    return i + 1


def _skip_regex(src: str, i: int) -> int:
    i, in_class = i + 1, False
    while True:
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            i += 1
            while i < len(src) and (src[i].isalpha()):
                i += 1  # flags
            return i
        i += 1


class _Scanner:
    """Walks JavaScript source as code: strings, template text, regexes and comments are skipped, template
    expressions are walked too. Records each call to an i18n helper as (name, index after its "(", line)."""

    def __init__(self, src: str):
        self.src, self.calls = src, []

    def code(self, i: int = 0, stop: bool = False) -> int:
        """Walk code from i to the end, or (stop) to the "}" that closes a template expression; return the index after."""
        src, n, depth, prev, prev_word = self.src, len(self.src), 0, "", ""
        while i < n:
            c = src[i]
            if c.isspace():
                i += 1
            elif src.startswith("//", i):
                j = src.find("\n", i)
                i = n if j < 0 else j
            elif src.startswith("/*", i):
                i = src.index("*/", i) + 2
            elif c in "\"'":
                i, prev, prev_word = _skip_string(src, i), '"', ""
            elif c == "`":
                i, prev, prev_word = self.template(i), '"', ""
            elif c == "/" and (prev in REGEX_BEFORE or prev == "" or prev_word in KEYWORDS_BEFORE_REGEX):
                i, prev, prev_word = _skip_regex(src, i), '"', ""
            elif c.isalnum() or c in "_$":
                j = i
                while j < n and (src[j].isalnum() or src[j] in "_$"):
                    j += 1
                word, k = src[i:j], j
                while k < n and src[k] in " \t":
                    k += 1
                if word in HELPERS and k < n and src[k] == "(" and prev != "." and prev_word != "function":
                    self.calls.append((word, k + 1, src.count("\n", 0, i) + 1))
                i, prev, prev_word = j, "a", word
            else:
                if stop and c == "{":
                    depth += 1
                elif stop and c == "}":
                    if depth == 0:
                        return i + 1
                    depth -= 1
                i, prev, prev_word = i + 1, c, ""
        return i

    def template(self, i: int) -> int:
        src, i = self.src, i + 1
        while src[i] != "`":
            if src[i] == "\\":
                i += 2
            elif src.startswith("${", i):
                i = self.code(i + 2, stop=True)
            else:
                i += 1
        return i + 1


def _args(src: str, i: int, count: int) -> list[str]:
    """The first `count` top-level arguments of the call whose "(" ends just before i, as source text."""
    args, start, depth, prev = [], i, 0, "("
    while len(args) < count:
        c = src[i]
        if c in "\"'":
            i, prev = _skip_string(src, i), '"'
            continue
        if c == "`":
            i, prev = _Scanner(src).template(i), '"'
            continue
        if c == "/" and prev in REGEX_BEFORE:
            i, prev = _skip_regex(src, i), '"'
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                args.append(src[start:i].strip())
                break
            depth -= 1
        elif c == "," and depth == 0:
            args.append(src[start:i].strip())
            start = i + 1
        if not c.isspace():
            prev = c if not (c.isalnum() or c in "_$") else "a"
        i += 1
    return args


def _calls(path: Path) -> list[tuple[str, list[str], int]]:
    src = path.read_text()
    scanner = _Scanner(src)
    scanner.code()
    return [(name, _args(src, i, max(HELPERS[name]) + 1), line) for name, i, line in scanner.calls]


def _keys() -> tuple[dict[str, int], list[str]]:
    """{key: first line used} for every literal key in app.js and i18n.js, and the problems: non-literal keys."""
    keys, problems = {}, []
    for path in (WEB / "app.js", WEB / "learning.js", WEB / "i18n.js"):
        for name, args, line in _calls(path):
            texts = [args[k] if k < len(args) else "" for k in HELPERS[name]]
            bad = [x for x in texts if not PLAIN.fullmatch(x)]
            if bad:
                if any(x.startswith("`") for x in bad) or path.name != "i18n.js":  # i18n.js's helpers pass variables on
                    problems.append(f"{path.name}:{line}: {name}() needs a plain \"...\" string, got {bad[0][:60]}")
                continue
            values = [json.loads(x) for x in texts]
            for key in ([f"{values[1]} [{values[0]}]"] if name == "tc" else values):
                keys.setdefault(key, line)
    return keys, problems


def _ja() -> dict[str, str]:
    src = (WEB / "ja.js").read_text()
    body = src[src.index("window.INTERLATCH_JA = {") + len("window.INTERLATCH_JA = ") : src.rindex("}") + 1]
    body = re.sub(r"^\s*//.*$", "", body, flags=re.M)
    body = re.sub(r",(\s*)\}$", r"\1}", body.rstrip())
    pairs = json.loads(body, object_pairs_hook=lambda kv: kv)
    dupes = sorted({k for k, _ in pairs if sum(1 for x, _ in pairs if x == k) > 1})
    assert not dupes, f"ja.js has duplicate keys: {dupes}"
    return dict(pairs)


def test_keys_are_plain_strings():
    _, problems = _keys()
    assert not problems, "\n".join(problems)


def test_the_scan_finds_the_calls():
    keys, _ = _keys()
    assert len(keys) > 900  # the scan reads the whole of app.js
    for key in ("Home", "{n} sessions", "Busiest hour: {when} · {n} prompts", "Added [date]", "Knowledge language"):
        assert key in keys


def test_every_key_has_a_japanese_translation():
    (keys, _), ja = _keys(), _ja()
    missing = [f"line {line}: {key!r}" for key, line in sorted(keys.items(), key=lambda kv: kv[1]) if key not in ja]
    assert not missing, "missing from ja.js:\n" + "\n".join(missing)


def test_placeholders_survive_translation():
    ja = _ja()
    broken = [f"{key!r} -> {value!r}" for key, value in ja.items()
              if set(PLACEHOLDER.findall(key)) != set(PLACEHOLDER.findall(value))]
    assert not broken, "placeholders differ:\n" + "\n".join(broken)


def test_no_unused_translations():
    keys, _ = _keys()
    unused = sorted(set(_ja()) - set(keys))
    assert not unused, "ja.js keys that app.js no longer uses:\n" + "\n".join(unused)


def test_translations_are_not_empty():
    for key, value in _ja().items():
        assert value.strip() or not key.strip(), f"empty translation for {key!r}"


def test_scripts_load_in_order():
    html = (WEB / "index.html").read_text()
    order = [html.index(f'src="{name}"') for name in ("ja.js", "i18n.js", "app.js")]
    assert order == sorted(order)
    assert 'id="lang-btn"' in html


NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="node is not installed")
@pytest.mark.parametrize("name", ["app.js", "i18n.js", "ja.js"])
def test_scripts_parse(name):
    proc = subprocess.run([NODE, "--check", str(WEB / name)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


HELPER_CHECK = r"""
const fs = require("fs"), vm = require("vm");
const run = (stored, browser) => {
  const store = stored ? { "interlatch-lang": stored } : {};
  const ctx = { window: {}, navigator: { languages: [browser], language: browser }, document: { documentElement: {} },
    localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; }, removeItem: (k) => { delete store[k]; } },
    location: { reload() {} } };
  vm.createContext(ctx);
  for (const f of ["ja.js", "i18n.js"]) vm.runInContext(fs.readFileSync(process.argv.at(-1) + "/" + f, "utf8"), ctx, { filename: f });
  return vm.runInContext(`({ lang: LANG, locale: LOCALE, html: document.documentElement.lang,
    home: t("Home"), missing: t("No such string {x}", { x: 1 }),
    one: tn(1, "{n} session", "{n} sessions"), many: tn(3, "{n} session", "{n} sessions", { n: "3" }),
    slots: tx("Busiest hour: {when} · {n} prompts", { when: "W", n: "5" }), ctx: tc("date", "Added"), plain: t("Added") })`, ctx);
};
console.log(JSON.stringify({ ja: run(null, "ja-JP"), en: run(null, "en-US"), stored: run("en", "ja-JP") }));
"""


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_helpers_pick_the_language_and_fill_placeholders():
    proc = subprocess.run([NODE, "-e", HELPER_CHECK, str(WEB)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    ja, en, stored = out["ja"], out["en"], out["stored"]
    assert (ja["lang"], ja["locale"], ja["html"]) == ("ja", "ja-JP", "ja")
    assert ja["home"] == "ホーム" and ja["one"] == "1 セッション" and ja["many"] == "3 セッション"
    assert ja["missing"] == "No such string 1"  # no translation: the English, filled in
    assert ja["slots"] == ["最も忙しい時間帯：", "W", " · ", "5", " プロンプト"]
    assert ja["ctx"] == "追加日" and ja["plain"] == "追加済み"
    assert (en["lang"], en["home"], en["one"], en["many"], en["ctx"]) == ("en", "Home", "1 session", "3 sessions", "Added")
    assert en["slots"] == ["Busiest hour: ", "W", " · ", "5", " prompts"]
    assert stored["lang"] == "en"  # the choice made in the dashboard wins over the browser's language
