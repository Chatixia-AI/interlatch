"""Japanese: [analysis] language (what Chronicle writes) and the dashboard's language for the server's own text."""

import ast
import json
import re
import string
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from friction_fixture import ERRORS, ago, make_archive

from chronicle import friction, i18n, ladder, suggest
from chronicle.config import DEFAULT_CONFIG_TOML, LANGUAGES, load_config, set_config_value
from chronicle.i18n import tr, trn
from chronicle.i18n_ja import JA
from chronicle.instructions import BEGIN, END
from chronicle.llm import JAPANESE, written_in

from conftest import CWD, SID, fake_log


@pytest.fixture()
def archive(tmp_path, monkeypatch):
    yield from make_archive(tmp_path, monkeypatch)


@pytest.fixture()
def ja():
    """The dashboard's language for one test, as a request with X-Interlatch-Lang: ja sets it."""
    token = i18n.lang.set("ja")
    yield
    i18n.lang.reset(token)


def _set_language(cfg, lang):
    set_config_value(cfg, "analysis", "language", json.dumps(lang))
    return load_config(cfg.home)


# ---------------------------------------------------------------- [analysis] language
def test_language_setting_defaults_parses_and_falls_back(env, caplog):
    cfg = env["cfg"]
    assert cfg.analysis.language == "en" and set(LANGUAGES) == {"en", "ja"}
    assert 'language = "en"' in DEFAULT_CONFIG_TOML
    assert _set_language(cfg, "ja").analysis.language == "ja"
    with caplog.at_level("WARNING"):
        assert _set_language(cfg, "fr").analysis.language == "en"
    assert "language 'fr'" in caplog.text


def test_cli_config_set_checks_the_language(env, capsys):
    from chronicle.cli import main

    assert main(["config", "set", "analysis.language", "de"]) == 2
    assert "must be one of: en, ja" in capsys.readouterr().err
    assert main(["config", "set", "analysis.language", "ja"]) == 0
    assert load_config(env["home"]).analysis.language == "ja"


def test_english_prompts_are_left_exactly_as_they_were(env):
    from chronicle import analyze, glossary, reviews, screen, synthesize

    cfg = env["cfg"]
    assert analyze.system_prompt(cfg) is analyze.SYSTEM_PROMPT
    for system in (synthesize.PROJECT_SYSTEM, synthesize.GLOBAL_SYSTEM, glossary.PROJECT_SYSTEM, glossary.THEMES_SYSTEM,
                   reviews.SYSTEM, screen.SYSTEM_PROMPT):
        assert written_in(cfg, system, "Write in English", "a note") is system
    # each English-writing sentence the Japanese prompt swaps out is really in its prompt
    assert analyze.WRITE_ENGLISH in analyze.SYSTEM_PROMPT and reviews.WRITE_ENGLISH in reviews.SYSTEM
    assert screen.WRITE_ENGLISH in screen.SYSTEM_PROMPT


def _systems(env) -> list[str]:
    out = []
    for call in fake_log(env):
        args = call["args"]
        out.append(args[args.index("--system-prompt") + 1])
    return out


def test_japanese_prompts_ask_for_japanese_and_keep_codes(synced):
    from chronicle import analyze, reviews
    from chronicle.analyze import analyze_session
    from chronicle.glossary import build_glossaries, build_themes
    from chronicle.reviews import generate_review
    from chronicle.synthesize import GLOBAL, synthesize_project

    conn = synced["conn"]
    cfg = _set_language(synced["cfg"], "ja")
    analyze_session(conn, cfg, SID)
    synthesize_project(conn, cfg, CWD)
    synthesize_project(conn, cfg, GLOBAL)
    build_glossaries(cfg, [CWD])
    for name in ("ruff", "mypy"):
        conn.execute("INSERT INTO glossary(term, norm, category, definition) VALUES (?, ?, 'tool', 'a tool')", (name, name))
    conn.commit()
    build_themes(cfg, ["tool"])
    generate_review(conn, cfg, "2026-W38")

    systems = _systems(synced)
    assert len(systems) == 6
    for system in systems:
        assert JAPANESE in system
        assert "in Japanese" in system and "verbatim" in system and "stay exactly as listed, in English" in system
        assert "Write in English" not in system
    analysis, kb, playbook, terms, themes, review = systems
    assert analyze.WRITE_ENGLISH not in analysis and "Tags stay short lowercase English terms" in analysis
    assert "「落とし穴と修正」" in kb and "different languages that state the same lesson are duplicates" in kb
    assert "「繰り返す問題」" in playbook and "different languages that state the same lesson are duplicates" in playbook
    assert "translations go in aliases" in terms
    assert "「その他」" in themes and reviews.WRITE_ENGLISH not in review
    # leftovers the theme pass did not place go to a theme named in the language Chronicle writes in
    assert {r[0] for r in conn.execute("SELECT name FROM glossary_themes WHERE category = 'tool'")} == {"Testing", "その他"}


def test_japanese_screening_prompt_and_rule_reasons(env):
    from chronicle.chat_import import import_export
    from chronicle.db import connect
    from chronicle.screen import screen_chats
    from test_screen import write_export

    cfg = _set_language(env["cfg"], "ja")
    conn = connect(cfg.db_path)
    import_export(cfg, conn, write_export(env["tmp"] / "chatgpt.zip"))
    screen_chats(cfg, conn)
    (system,) = _systems(env)
    assert "write topic and reason in Japanese" in system and "write topic and reason in English" not in system
    row = conn.execute("SELECT screen_topic, screen_reason FROM sessions WHERE id = 'c-short'").fetchone()
    assert tuple(row) == ("短すぎる", "分析できる内容がほとんどない")
    conn.close()


def test_overturned_lines_follow_the_review_language():
    from chronicle.reviews import overturned_line

    k = {"title": "Use uv sync", "project_name": "app", "stage": "established", "superseded_reason": "outdated",
         "successor_title": "Use uv sync --extra app"}
    assert overturned_line(k) == "Use uv sync (app): was established, outdated; now “Use uv sync --extra app”"
    assert overturned_line(k, "ja") == "Use uv sync (app)：定着（古くなった） →「Use uv sync --extra app」"


# ---------------------------------------------------------------- Japanese friction notes
JA_NOTES = {
    "playwright-output-roots": ["Playwright MCP でスクリーンショットをスクラッチパッドの絶対パスに保存しようとして、許可されたルートの外だとして拒否された",
                                "file:// の URL がブロックされ、ローカルの HTML をプレビューできなかった",
                                "スクリーンショットの保存先が許可されておらず、保存に何度も失敗した"],
    "playwright-browser-in-use": ["Playwright のブラウザーが使用中のため操作できず、ロックファイルを消そうとした",
                                  "Chrome のプロファイルがロックされていて Playwright MCP のブラウザを起動できなかった"],
    "playwright-stale-refs": ["ページ遷移後に古いスナップショットの ref を使ってクリックし、ref が見つからないエラーになった",
                              "セレクターが曖昧で複数の要素に一致し、クリックが失敗した"],
    "edit-stale-context": ["読み込んだ後にファイルが変更されていたため、Edit が失敗した",
                           "apply_patch で想定した行が見つからず、パッチの適用に何度も失敗した",
                           "置換対象の文字列が見つからず編集が失敗し、ファイルを読み直す必要があった"],
    "zsh-nomatch": ["引用符で囲まないワイルドカードが何にも一致せず、zsh がコマンド全体を中断した",
                    "zsh の glob が一致しないため grep --include の指定がエラーになった"],
    "zsh-equals": ["echo で区切り線を出そうとして、= で始まる語が zsh に展開されてエラーになった"],
    "zsh-dialect": ["zsh では status が読み取り専用の変数なので、スクリプトがエラーで止まった",
                    "変数に入れたオプションが zsh では単語分割されず、コマンドが失敗した"],
    "sleep-polling-blocked": ["sleep 60 を挟んでビルドの完了を待とうとしたが、Claude Code にブロックされた"],
    "timeout-missing": ["macOS に timeout コマンドがなく、終了コード 127 で失敗した",
                        "timeout が見つからないため、テストの実行を時間で区切れなかった"],
    "bare-python-modules": ["システムの Python に python-pptx がインストールされておらず、スクリプトが動かなかった",
                            "python3 でスクリプトを実行したが openpyxl が入っておらず失敗した"],
    "interactive-aliases": ["cp が確認付きのエイリアスになっていて、ファイルが上書きされなかった",
                            "rm の対話的な確認プロンプトでコマンドが止まった"],
    "stale-dev-server": ["ポート 8765 がすでに使われていて、動作確認では古いサーバーが応答していた",
                         "前のセッションで起動したままの開発サーバーが古いコードを返していた"],
    "cwd-drift": ["シェルの作業ディレクトリが前のコマンドのまま残っていて、相対パスの cd backend が失敗した",
                  "違うディレクトリからテストを実行してしまい、ファイルが見つからなかった"],
    "auto-mode-retry": ["オートモードの分類器に拒否された操作を、言い換えて何度も再試行した"],
    "bash-timeout": ["find ~ による検索が 2 分のタイムアウトに達して中断された",
                     "長いビルドコマンドが Bash のタイムアウトで打ち切られた"],
    "concurrent-sessions": ["同じリポジトリで並行して動いていた別のエージェントがファイルを書き換えていた",
                            "他のセッションが同じ作業ツリーでコミットしていたため、差分に無関係な変更が混ざった"],
}


@pytest.mark.parametrize("cause", sorted(JA_NOTES))
def test_japanese_notes_name_their_cause(cause):
    for note in JA_NOTES[cause]:
        assert friction.match_note(note) == [cause], note


def test_every_wasteful_cause_has_japanese_patterns_and_notes():
    for c in friction.CATALOG:
        if not c["noise"]:
            assert any(re.search(r"[^\x00-\x7f]", p) for p in c["friction_patterns"]), c["id"]
            assert c["id"] in JA_NOTES, c["id"]


def test_japanese_patterns_stay_out_of_other_notes():
    english = [text for _tool, text, _cmd in ERRORS.values()] + [
        "Multiple Bash command failures early on: `timeout` not found on macOS, `--include=*.ts` glob syntax not "
        "supported by the shell, and shell cwd being silently reset between calls",
        "Repeated 529 Overloaded errors blocked the session", "The developer changed their mind about the colour scheme",
        "Vendor sandbox rejected the invoice upload with code 400"]
    japanese = [p for c in friction.CATALOG for p in c["friction_patterns"] if re.search(r"[^\x00-\x7f]", p)]
    for p in japanese:
        assert not [t for t in english if re.search(p, t, re.I)], p
    for note in ("開発者が配色についての考えを変えた", "API の仕様が分からず調査に時間がかかった", "テストが 3 件失敗したので修正した",
                 "npm install が遅く 5 分かかった", "要件の誤解で実装をやり直した", "型エラーの修正に時間がかかった",
                 "ドキュメントの編集方針について何度か確認が必要だった", "ログインページの文言を何度も修正した"):
        assert friction.match_note(note) == [], note
    assert friction.match_note("grepコマンドで--include=*.mdオプションの引数展開がシェルにより失敗し(Exit code 1: no matches found)") \
        == ["zsh-nomatch"]


# ---------------------------------------------------------------- instruction lines in Japanese
def test_every_instruction_fix_has_a_japanese_line():
    for c in friction.CATALOG:
        for fix in c["fixes"]:
            if fix["kind"] == "instruction":
                assert re.search(r"[ぁ-んァ-ン一-龥]", fix.get("text_ja") or ""), (c["id"], fix["title"])
                # commands, flags and paths are kept verbatim
                for code in re.findall(r"`[^`]+`", fix["text"]):
                    if code not in ("`sleep N`",):
                        assert code in fix["text_ja"], (c["id"], code)
            else:
                assert "text_ja" not in fix  # commands and config changes are the same in every language


def _causes(archive, *causes):
    a = archive["a"]
    for cause in causes:
        for i in range(3):
            sid = f"{cause}-{i}"
            a.session(sid, a.project(f"proj{i}"))
            a.cause(sid, cause, ago(1))
    archive["conn"].commit()


def _lines(conn) -> dict[str, dict]:
    return {s["cause_id"]: s for s in suggest.list_suggestions(conn) if s["kind"] == "instruction"}


def test_suggestions_are_written_in_the_language_and_switch_until_edited(archive):
    conn, cfg = archive["conn"], archive["cfg"]
    _causes(archive, "zsh-nomatch", "sleep-polling-blocked")
    suggest.refresh(conn, cfg)
    lines = _lines(conn)
    nomatch, sleep = friction.BY_ID["zsh-nomatch"]["fixes"][1], friction.BY_ID["sleep-polling-blocked"]["fixes"][0]
    assert lines["zsh-nomatch"]["text"] == nomatch["text"] and lines["sleep-polling-blocked"]["text"] == sleep["text"]
    assert suggest.edit_text(conn, lines["sleep-polling-blocked"]["id"], "My own wording.")["ok"]

    cfg.analysis.language = "ja"
    suggest.refresh(conn, cfg)
    lines = _lines(conn)
    assert lines["zsh-nomatch"]["text"] == nomatch["text_ja"]  # waiting, untouched: now in Japanese
    assert lines["zsh-nomatch"]["evidence"]["generated_text"] == nomatch["text_ja"]
    assert lines["sleep-polling-blocked"]["text"] == "My own wording."  # yours is never replaced
    setup = next(s for s in suggest.list_suggestions(conn) if s["kind"] == "environment" and s["cause_id"] == "zsh-nomatch")
    assert setup["text"] == "echo 'setopt NO_NOMATCH' >> ~/.zshrc"  # a command reads the same in every language

    applied = lines["zsh-nomatch"]
    assert suggest.apply(conn, cfg, applied["id"])["ok"]
    target = Path(applied["target_path"])
    assert f"{nomatch['text_ja']} <!-- interlatch:friction:zsh-nomatch -->" in target.read_text()
    cfg.analysis.language = "en"
    suggest.refresh(conn, cfg)
    assert suggest.get(conn, applied["id"])["text"] == nomatch["text_ja"]  # an applied line stays as it was written


@pytest.mark.parametrize("written, now", [("text", "ja"), ("text_ja", "en")])
def test_a_line_already_in_the_file_is_not_proposed_again_in_the_other_language(archive, written, now):
    conn, cfg, home = archive["conn"], archive["cfg"], archive["home"]
    fix = friction.BY_ID["zsh-nomatch"]["fixes"][1]
    target = home / ".claude" / "CLAUDE.md"
    target.write_text(f"# Mine\n\n{BEGIN}\n- {fix[written]} <!-- interlatch:friction:zsh-nomatch -->\n{END}\n")
    _causes(archive, "zsh-nomatch")
    cfg.analysis.language = now
    suggest.refresh(conn, cfg)
    assert "zsh-nomatch" not in _lines(conn)  # the marker is the same in both languages
    assert any(s["kind"] == "environment" for s in suggest.list_suggestions(conn))


def test_a_line_outside_the_block_counts_in_either_language(archive):
    conn, cfg, home = archive["conn"], archive["cfg"], archive["home"]
    fix = friction.BY_ID["zsh-nomatch"]["fixes"][1]
    (home / ".claude" / "CLAUDE.md").write_text(f"- {fix['text']}\n")
    _causes(archive, "zsh-nomatch")
    cfg.analysis.language = "ja"
    suggest.refresh(conn, cfg)
    assert "zsh-nomatch" not in _lines(conn)


def test_japanese_knowledge_lines_compare_and_read_as_rules():
    from chronicle.instructions import JACCARD_DUPLICATE, is_changelog, is_rule_like, jaccard, overlap, tokens

    assert {"編集", "直前", "該当", "当行"} <= tokens("編集の直前に該当行を読み直す")
    assert tokens("Re-read the exact lines") == {"re-read", "exact", "lines"}  # English as before
    # one shared identifier no longer makes two different Japanese lessons the same one
    a, b = tokens("Docker のキャッシュを消してから再ビルドする"), tokens("Docker のネットワークは host を使わない")
    assert jaccard(a, b) < JACCARD_DUPLICATE
    assert jaccard(tokens("Docker のキャッシュを消して再ビルドする"), a) >= JACCARD_DUPLICATE
    assert overlap("編集の直前に該当行を読み直す。", "# メモ\n- 編集の直前に該当行を必ず読み直す\n")
    assert is_rule_like("Docker のネットワークは host を使わない") and is_rule_like("テストは必ず uv run pytest で実行する")
    assert not is_rule_like("ログインの不具合")
    assert is_changelog("ログインの不具合を修正した") and is_changelog("キャッシュを追加") and not is_changelog("ポートを確認する")


# ---------------------------------------------------------------- the dashboard's language
def test_tr_and_trn_fall_back_to_english(ja):
    assert tr("not found") == "見つかりません"
    assert tr("no such phrase {x}", x=1) == "no such phrase 1"  # no translation: English
    assert tr("{n} in this project", n=3) == "このプロジェクトで 3"
    assert trn(1, "seen in {n} session", "seen in {n} sessions") == "1 セッションで発生"  # one form in Japanese
    assert trn(2, "{n} thing", "{n} things") == "2 things"
    token = i18n.lang.set("en")
    try:
        assert tr("not found") == "not found"
        assert trn(1, "seen in {n} session", "seen in {n} sessions") == "seen in 1 session"
    finally:
        i18n.lang.reset(token)
    assert [i18n.pick(h) for h in ("ja", " JA ", "en", "fr", None)] == ["ja", "ja", "en", "en", "en"]


def test_every_translated_template_has_japanese_with_the_same_fields():
    from chronicle import update, views, worker

    need = set()
    for path in sorted((Path(i18n.__file__).parent).glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "tr" and node.args and isinstance(node.args[0], ast.Constant):
                    need.add(node.args[0].value)
                if node.func.id == "trn" and len(node.args) >= 3 and isinstance(node.args[2], ast.Constant):
                    need.add(node.args[2].value)
    for c in friction.CATALOG:  # translated where they are shown (friction.display, suggest.display)
        need.add(c["name"])
        need.update(f["title"] for f in c["fixes"])
    need.update([friction.CONCURRENT_NOTE, update.PYPI_ERROR, update.MOVE_NOTE, "user level", *ladder.STAGES, "pinned by you", "added by you",
                 ladder.MEMORY_STAGE_REASON, "one session, low confidence", "one session", *worker.QUEUE_REASON_LABEL.values(),
                 *views.ANALYSIS_REASONS, views.NOT_ANALYZED_CHAT])
    assert sorted(need - set(JA)) == []
    fields = lambda s: {f for _, f, _, _ in string.Formatter().parse(s) if f}
    for en, ja_text in JA.items():
        assert fields(en) == fields(ja_text), en


def test_stored_english_templates_are_translated_when_read(ja):
    from chronicle.chat_import import CHATGPT
    from chronicle.views import reason_text

    assert reason_text("session continued after analysis") == "分析のあとにセッションが続いた"
    assert reason_text(CHATGPT.not_analyzed).startswith("取り込んだ ChatGPT のチャット：")
    assert reason_text("claude -p exited 1: overloaded") == "claude -p exited 1: overloaded"  # the analyzer's own words
    assert ladder.reason_text("confirmed in 3 sessions over 19 days (2026-09-01 → 2026-09-20)") == \
        "19 日間の 3 セッションで確認（2026-09-01 → 2026-09-20）"
    assert ladder.reason_text("confirmed in 2 sessions") == "2 セッションで確認"
    assert ladder.reason_text("pinned by you") == "あなたがピン留め"
    assert ladder.reason_text("something a newer version wrote") == "something a newer version wrote"
    assert friction.display_note("while session abcd1234 was active here: address already in use") == \
        "セッション abcd1234 がここで動いていた間：address already in use"


def test_status_texts_follow_the_dashboard_language(synced, ja):
    from chronicle.connectors import all_status
    from chronicle.worker import count_pending

    cfg, conn = synced["cfg"], synced["conn"]
    claude = next(c for c in all_status(cfg, conn) if c["name"] == "claude")
    checks = {k["key"]: k for k in claude["checks"]}
    assert checks["mcp"]["label"] == "Claude Code の MCP サーバー" and checks["recording"]["detail"] == "同期のたびに読み込み"
    cfg.analysis.auto = False
    pending = count_pending(conn, cfg)
    assert pending["block"].startswith("自動分析がオフです（analysis.auto）")
    assert pending["labels"]["active"] == "まだ作業中" and pending["labels"]["ready"] == "準備完了"


def test_cli_and_stored_text_stay_english(archive):
    conn, cfg = archive["conn"], archive["cfg"]
    _causes(archive, "zsh-nomatch")
    token = i18n.lang.set("ja")  # a refresh asked for by a Japanese dashboard stores English all the same
    try:
        suggest.refresh(conn, cfg)
    finally:
        i18n.lang.reset(token)
    line = _lines(conn)["zsh-nomatch"]
    assert line["title"] == "Quote every glob: the shell is zsh" and line["evidence"]["line"].startswith("seen in 3 sessions")
    assert suggest.display(line) is line  # no header: English, untouched


def _serve(cfg):
    from chronicle.server import App, make_handler

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
    httpd.RequestHandlerClass = make_handler(App(cfg), httpd.server_address[1])
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _call(base, path, body=None, *, lang=None, write=True):
    headers = {"Content-Type": "application/json", **({"X-Interlatch": "1"} if write else {}),
               **({"X-Interlatch-Lang": lang} if lang else {})}
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method="GET" if body is None else "POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_api_answers_in_the_dashboards_language(archive):
    conn, cfg = archive["conn"], archive["cfg"]
    _causes(archive, "zsh-nomatch")
    suggest.refresh(conn, cfg)
    httpd, base = _serve(cfg)
    try:
        _, en = _call(base, "/api/suggestions")
        _, ja_ = _call(base, "/api/suggestions", lang="ja")
        pick = lambda got: next(s for s in got["suggestions"] if s["kind"] == "instruction")
        assert pick(en)["title"] == "Quote every glob: the shell is zsh"
        assert pick(en)["evidence"]["line"].startswith("seen in 3 sessions across 3 projects · still happening · last ")
        assert pick(ja_)["title"] == "glob はすべて引用符で囲む：シェルは zsh"
        assert pick(ja_)["evidence"]["line"].startswith("3 プロジェクトの 3 セッションで発生 · まだ起きている · 最終 ")
        assert pick(ja_)["text"] == pick(en)["text"]  # the line itself follows [analysis] language, not the dashboard

        _, causes = _call(base, "/api/friction", lang="ja")
        cause = next(c for c in causes["causes"] if c["id"] == "zsh-nomatch")
        assert cause["name"].startswith("引用符のない glob") and cause["fixes"][1]["title"].startswith("glob はすべて")
        assert _call(base, "/api/nope", lang="ja") == (404, {"error": "見つかりません"})
        assert _call(base, "/api/nope", lang="xx") == (404, {"error": "not found"})
        assert _call(base, "/api/suggestions/99999/preview", lang="ja")[1]["error"] == "その提案はありません"
        assert _call(base, "/api/nope") == (404, {"error": "not found"})  # each request on its own: back to English
    finally:
        httpd.shutdown()


def test_api_sets_the_analysis_language(env):
    cfg = env["cfg"]
    httpd, base = _serve(cfg)
    try:
        status, got = _call(base, "/api/analysis/language", {"language": "ja"})
        assert status == 200 and got["language"] == "ja" and got["backend"] == "claude"
        assert got["languages"] == [{"code": "en", "label": "English"}, {"code": "ja", "label": "日本語"}]
        assert 'language = "ja"' in cfg.config_path.read_text()
        assert load_config(env["home"]).analysis.language == "ja"
        assert _call(base, "/api/status")[1]["analysis"]["language"] == "ja"

        assert _call(base, "/api/analysis/language", {"language": "fr"}, lang="ja")[1] == \
            {"error": "'fr' という言語は選べません"}
        assert _call(base, "/api/analysis/language", {"language": "fr"})[1] == {"error": "unknown language 'fr'"}
        assert _call(base, "/api/analysis/language", {"language": "en"}, write=False)[0] == 403
        assert _call(base, "/api/analysis/language", {"language": "en"})[1]["language"] == "en"
    finally:
        httpd.shutdown()
