import json
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from deepwick_spec.ansi import line_with, runs
from deepwick_spec.check_port import check_port
from deepwick_spec.palette import load_palette
from deepwick_spec.tui import capture, tmux_available

REPO = Path(__file__).resolve().parents[1]
PALETTE = load_palette()
C = PALETTE.colors
THEME = REPO / "deepwick.json"
SECTIONS = {
    "styles": {"adjusted-time", "alt-text", "breadcrumb", "cursor-line", "disabled-cursor-line", "disabled-focused",
               "error", "file-offset", "focused", "footnote-border", "footnote-text", "fuzzy-match", "h1", "h2", "h3",
               "h4", "h5", "h6", "hidden", "hr", "hyperlink", "identifier", "indent-guide", "info", "invalid-msg",
               "list-glyph", "offset-time", "ok", "popup", "popup-border", "quote-border", "quoted-text", "scrollbar",
               "selected-text", "skewed-time", "snippet-border", "table-border", "table-header", "text", "time-column",
               "warning"},
    "syntax-styles": {"ascii-control", "code-border", "comment", "diff-add", "diff-delete", "diff-section",
                      "doc-directive", "file", "function", "inline-code", "keyword", "non-ascii", "null", "number",
                      "object-key", "quoted-code", "re-repeat", "re-special", "separators-references-accessors",
                      "spectrogram-high", "spectrogram-low", "spectrogram-medium", "string", "symbol", "type", "variable"},
    "status-styles": {"active", "alert", "disabled-title", "hotkey", "inactive", "inactive-alert", "info", "subtitle",
                      "suggestion", "text", "title", "title-hotkey", "warn"},
    "log-level-styles": {"critical", "debug", "debug2", "debug3", "debug4", "debug5", "error", "fatal", "info",
                         "invalid", "notice", "stats", "trace", "warning"},
}
SAMPLE = """\
2026-10-14T12:00:00.000Z [INFO] deepwick.app: service started on 127.0.0.1 port 8080
2026-10-14T12:00:01.000Z [DEBUG] deepwick.pool: opened 4 connections
2026-10-14T12:00:02.000Z [WARNING] deepwick.pool: pool at 90% capacity
2026-10-14T12:00:03.000Z [ERROR] deepwick.db: query failed: "SELECT 1"
2026-10-14T12:00:04.000Z [CRITICAL] deepwick.app: shutting down
"""
LNAV = shutil.which("lnav")
LNAV_PATH = f"{Path(LNAV).parent}:/usr/bin:/bin" if LNAV else "/usr/bin:/bin"
requires_lnav = pytest.mark.skipif(LNAV is None, reason="requires lnav")



def isolated_env(tmp_path, **extra):
    dirs = {"HOME": tmp_path / "home"}
    for kind in ("CONFIG", "DATA", "STATE", "CACHE"):
        dirs[f"XDG_{kind}_HOME"] = tmp_path / kind.lower()
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    env = {key: str(path) for key, path in dirs.items()}
    env.update({key: str(value) for key, value in extra.items()})
    return env


def style_containing(text, needle):
    for chunk, style in runs(text):
        if needle in chunk:
            return style
    raise AssertionError(f"no run contains {needle!r}")


def has_bg(text, name):
    return any(style["bg"] == C[name].lower() for _, style in runs(text))


def has_fg(text, name):
    return any(style["fg"] == C[name].lower() for _, style in runs(text))


def theme():
    assert THEME.is_file(), "deepwick.json missing"
    return json.loads(THEME.read_text(encoding="utf-8"))["ui"]["theme-defs"]["deepwick"]


def var_name(value):
    return value[1:] if value.startswith("$") else None


def test_vars_are_palette_colors():
    allowed = {v.lower() for v in C.values()}
    assert all(v.lower() in allowed for v in theme()["vars"].values())


def test_every_key_is_set():
    t = theme()
    for section, keys in SECTIONS.items():
        assert set(t[section]) == keys, section


def test_style_colors_reference_vars():
    t = theme()
    styles = [s for section in SECTIONS for s in t[section].values()] + [h["style"] for h in t["highlights"].values()]
    for style in styles:
        for prop in ("color", "background-color"):
            if prop in style:
                assert var_name(style[prop]) in t["vars"], style


def resolved(section, key):
    t = theme()
    style = t[section][key]
    return {prop: t["vars"][var_name(style[prop])] for prop in ("color", "background-color") if prop in style}


def test_roles():
    assert resolved("log-level-styles", "error") == {"color": C["red"]}
    assert resolved("log-level-styles", "warning") == {"color": C["yellow"]}
    assert theme()["log-level-styles"]["fatal"].get("bold") is True
    assert resolved("syntax-styles", "keyword") == {"color": C["blue"]}
    assert resolved("syntax-styles", "string") == {"color": C["orange"]}
    assert theme()["syntax-styles"]["comment"] == {"color": "$comment", "italic": True}
    assert resolved("status-styles", "title") == {"color": C["background"], "background-color": C["blue"]}
    assert resolved("styles", "cursor-line") == {"color": C["foreground"], "background-color": C["selection"]}
    assert theme()["status-styles"]["title-hotkey"].get("bold") is True
    assert resolved("log-level-styles", "debug") == {"color": C["comment"]}


def lnav_env(tmp_path):
    env = isolated_env(tmp_path, TERM="tmux-256color", TZ="UTC", TMPDIR=tmp_path / "tmp")
    Path(env["TMPDIR"]).mkdir()
    config = Path(env["XDG_CONFIG_HOME"]) / "lnav"
    (config / "configs" / "deepwick").mkdir(parents=True)
    shutil.copy(THEME, config / "configs" / "deepwick" / "deepwick.json")
    log = tmp_path / "sample.log"
    log.write_text(SAMPLE)
    # Activate the theme the way the README says.
    subprocess.run(["lnav", "-n", "-c", ":config /ui/theme deepwick", str(log)],
                   env={**env, "PATH": LNAV_PATH}, capture_output=True, text=True, check=True, timeout=30)
    return env, log


@requires_lnav
def test_key_sets_match_installed_lnav(tmp_path):
    env, _ = lnav_env(tmp_path)
    dump = json.loads(subprocess.run(["lnav", "-m", "config", "get"], env={**env, "PATH": LNAV_PATH},
                                     capture_output=True, text=True, check=True).stdout)
    default = dump["ui"]["theme-defs"]["default"]
    for section, keys in SECTIONS.items():
        assert set(default[section]) == keys, section


@requires_lnav
def test_lnav_accepts_theme(tmp_path):
    env, log = lnav_env(tmp_path)
    result = subprocess.run(["lnav", "-W", "-n", "-c", ":echo ok", str(log)], env={**env, "PATH": LNAV_PATH},
                            capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "warning" not in output.lower() and "error" not in output.lower(), output


@pytest.mark.skipif(not (tmux_available() and shutil.which("lnav")), reason="requires tmux and lnav")
def test_lnav_renders_levels(tmp_path):
    env, log = lnav_env(tmp_path)
    out = capture(f"lnav {log}", env, size=(120, 30), wait_for="shutting down", timeout=20)
    assert style_containing(line_with(out, "query failed"), "query failed")["fg"] == C["red"].lower()
    assert style_containing(line_with(out, "capacity"), "capacity")["fg"] == C["yellow"].lower()
    assert has_bg(out, "blue"), "status title should be on Blue"
    assert any(s["bg"] == C["surface"].lower() for _, s in runs(out))


def test_port_conventions():
    assert check_port(REPO, PALETTE.allowed_hex()) == []


@pytest.mark.skipif(not (tmux_available() and shutil.which("lnav")), reason="requires tmux and lnav")
def test_lnav_help_headings(tmp_path):
    env, log = lnav_env(tmp_path)
    out = capture(f"lnav {log}", env, size=(120, 40), wait_for="shutting down", keys=("?",), after="Overview", timeout=20)
    heading = style_containing(line_with(out, "Overview"), "Overview")
    assert (heading["fg"], heading["bold"]) == (C["blue"].lower(), True)


@pytest.mark.skipif(not (tmux_available() and shutil.which("lnav")), reason="requires tmux and lnav")
def test_lnav_sql_prompt_syntax(tmp_path):
    env, log = lnav_env(tmp_path)
    # :prompt pre-fills the SQL prompt; typing ';' and the query races lnav's mode switch.
    command = shlex.join(["lnav", "-c", ":prompt sql ; 'SELECT \\'abc\\', 42 FROM t -- c'", str(log)])
    out = capture(command, env, size=(120, 40), wait_for="-- c", timeout=20)
    line = line_with(out, "'abc'")
    assert style_containing(line, "'abc'")["fg"] == C["orange"].lower()
    assert style_containing(line, "42")["fg"] == C["sage"].lower()
    assert style_containing(line, "FROM")["fg"] == C["blue"].lower()
    assert style_containing(line, "-- c")["fg"] == C["comment"].lower()
