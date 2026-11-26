"""Optional AI boost: pick the funniest/most viral moments and write short funny titles.

Modes (the buyer chooses; default off):
  off     - local picker only, nothing leaves the PC
  api     - the buyer's own Claude API key (kept in Windows Credential Manager)
  claude  - the Claude Code app already installed on this PC (`claude -p`)
  codex   - the Codex app already installed on this PC (`codex exec`)
  gemini  - the Gemini CLI already installed on this PC (`gemini -p`, free with a Google account)
  gemini_api - the buyer's own Gemini API key from aistudio.google.com (free tier), in Credential Manager

"Installed" = the program is on this PC's PATH (shutil.which), the same way a terminal finds it.
Antigravity is an editor without a command another app can call, so it can't be used here; its
Google sign-in works with the Gemini CLI.

Only short transcript snippets and timings are sent, never the video.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import vault

MODES = ("off", "api", "claude", "codex", "gemini", "gemini_api", "local")
OLLAMA = "http://127.0.0.1:11434"
KEY_NAME = "anthropic_api_key"
GEMINI_KEY_NAME = "gemini_api_key"
MODEL = "claude-opus-5-5"
GEMINI_MODEL = "gemini-flash-latest"
# Google retires model ids for newly made keys and flash ids answer 503 when busy, so the Gemini key
# calls walk this list: newest general flash first, then older ones that still accept new keys.
GEMINI_MODELS = (GEMINI_MODEL, "gemini-3.8-flash", "gemini-3.6-flash", "gemini-flash-lite-latest", "gemini-2.5-flash")
GEMINI_TRY_SECONDS = 60  # one busy model must not use up the whole wait
GEMINI_TOTAL_SECONDS = 150
GEMINI_KEYS_URL = "aistudio.google.com/apikey"
SCHEMA = {
    "type": "object",
    "properties": {
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "score": {"type": "integer"},
                    "title": {"type": "string"},
                },
                "required": ["id", "score", "title"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["moments"],
    "additionalProperties": False,
}
SYSTEM = (
    "You are a short-form video editor for gaming streams (GTA RP, Valorant, Minecraft and similar). "
    "You get moments from one stream: an id, how long it is, a loudness/motion score from 0 to 1, and what was said. "
    "For each moment give a score from 0 to 10 for how likely it is to do well as a Short or Reel: "
    "funny, surprising, dramatic, a clear story in a few seconds. Quiet filler or setup with no payoff scores low. "
    "Speech may be in Hindi, Hinglish, Kannada or another language: understand it and judge it the same way. "
    "Then write a title for each: 3 to 8 words in English, plain and funny, the way a viewer would describe it to a friend, "
    "like \"Kai Cenat's rizz goes wrong\". Only describe what the words show happened. "
    "If a moment has no clear speech, score it on energy alone and title it with the game and a hype word, "
    "like \"Crazy GTA moment\"; never write about waiting, silence or nothing happening. "
    "Use the streamer's name only if given. No hashtags, no emoji, "
    "no quotation marks around the whole title, no clickbait words like INSANE or CRAZY in capitals. "
    "Keep swearing out of titles. Return every id you were given."
)
# Gemini has no schema to lean on here, so spell the shape out; parse_picks takes a bare list too.
SHAPE = 'Answer with only this JSON and nothing else: {"moments": [{"id": "m1", "score": 8, "title": "short funny title"}]}'


class BoostError(RuntimeError):
    pass


@dataclass
class Pick:
    id: str
    score: int
    title: str


def _clean_title(text: str) -> str:
    text = " ".join(str(text).split()).strip().strip('"').strip("“”").strip()
    return text[:90]


def build_prompt(moments: list[dict], streamer: str = "", topic: str = "") -> str:
    lines = []
    if streamer:
        lines.append(f"Streamer: {streamer}")
    if topic:
        lines.append(f"Game or topic: {topic}")
    lines.append("Moments:")
    for m in moments:
        said = " ".join(str(m.get("text") or "").split())[:700] or "(no clear speech: judge by energy)"
        lines.append(f"- id {m['id']} | {m['end'] - m['start']:.0f}s | energy {m.get('score', 0):.2f} | said: {said}")
    return "\n".join(lines)


def _json_block(text: str) -> str:
    """The outer {...} or [...] in a chatty answer, whichever comes first."""
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        return ""
    start = min(starts)
    end = text.rfind("]" if text[start] == "[" else "}")
    return text[start : end + 1] if end > start else ""


def parse_picks(data, ids: set[str]) -> list[Pick]:
    if isinstance(data, str):
        # Gemini answers a bare [ ... ] list even when asked for {"moments": [...]}; small local
        # models add chat around it. Try the whole text, then the outer block.
        for attempt in (data.strip(), _json_block(data)):
            if not attempt:
                continue
            try:
                data = json.loads(attempt)
                break
            except ValueError:
                continue
        else:
            raise BoostError("The AI didn't send back a list of moments.")
    picks = []
    if isinstance(data, dict) and ("id" in data or "title" in data):
        rows = [data]  # one moment, sent on its own
    elif isinstance(data, dict) and isinstance(data.get("moments"), list):
        rows = data["moments"]
    elif isinstance(data, dict):
        # small local models answer {"m1": {"score": 8, "title": ...}, ...}
        rows = [dict(v, id=v.get("id") or k) for k, v in data.items() if isinstance(v, dict)]
    else:
        rows = data
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        ident = str(row.get("id", ""))
        if ident in ids and row.get("title"):
            try:
                score = max(0, min(10, int(row.get("score", 0))))
            except (TypeError, ValueError):
                score = 0
            picks.append(Pick(ident, score, _clean_title(row["title"])))
    if not picks:
        raise BoostError("The AI didn't rate any moments.")
    return picks


# ---------------- the three ways to ask ----------------
def _ask_api(prompt: str, key: str, client=None) -> object:
    try:
        import anthropic
    except ImportError as error:  # pragma: no cover
        raise BoostError("The Claude library is missing from this install.") from error
    client = client or anthropic.Anthropic(api_key=key, max_retries=2, timeout=120)
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError as error:
        raise BoostError("Claude didn't accept that API key.") from error
    except anthropic.PermissionDeniedError as error:
        raise BoostError("That Claude key isn't allowed to use this model.") from error
    except anthropic.RateLimitError as error:
        raise BoostError("Claude says slow down (rate limit). The normal picker was used.") from error
    except anthropic.APIStatusError as error:
        raise BoostError(f"Claude had a problem ({error.status_code}). The normal picker was used.") from error
    except anthropic.APIConnectionError as error:
        raise BoostError("Couldn't reach Claude. Check your internet.") from error
    if response.stop_reason == "refusal":
        raise BoostError("Claude declined this one. The normal picker was used.")
    if response.stop_reason == "max_tokens":
        raise BoostError("Claude's answer was cut off. The normal picker was used.")
    return next((b.text for b in response.content if b.type == "text"), "")


def _no_window():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ask_claude_cli(prompt: str, timeout: int = 240) -> object:
    exe = find_app("claude")
    if not exe:
        raise BoostError("The Claude app isn't installed on this PC.")
    with tempfile.TemporaryDirectory(prefix="clipstudio-ai-") as folder:
        cmd = [exe, "-p", "--output-format", "json", "--json-schema", json.dumps(SCHEMA),
               "--system-prompt", SYSTEM, "--tools", "", "--strict-mcp-config", "--no-session-persistence"]
        try:
            run = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                                 timeout=timeout, cwd=folder, creationflags=_no_window())
        except subprocess.TimeoutExpired as error:
            raise BoostError("The Claude app took too long. The normal picker was used.") from error
    if run.returncode != 0:
        raise BoostError("The Claude app didn't answer. Open it once and sign in, then try again.")
    try:
        envelope = json.loads(run.stdout)
    except ValueError:
        return run.stdout
    if envelope.get("is_error"):
        raise BoostError("The Claude app said: " + str(envelope.get("result", "error"))[:160])
    return envelope.get("structured_output") or envelope.get("result") or ""


def _ask_codex_cli(prompt: str, timeout: int = 300) -> object:
    exe = find_app("codex")
    if not exe:
        raise BoostError("The Codex app isn't installed on this PC.")
    with tempfile.TemporaryDirectory(prefix="clipstudio-ai-") as folder:
        schema = Path(folder) / "schema.json"
        schema.write_text(json.dumps(SCHEMA), encoding="utf-8")
        answer = Path(folder) / "answer.txt"
        cmd = [exe, "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
               "--output-schema", str(schema), "-o", str(answer), "-"]
        try:
            run = subprocess.run(cmd, input=SYSTEM + "\n\n" + prompt, capture_output=True, text=True,
                                 encoding="utf-8", timeout=timeout, cwd=folder, creationflags=_no_window())
        except subprocess.TimeoutExpired as error:
            raise BoostError("The Codex app took too long. The normal picker was used.") from error
        if run.returncode != 0 or not answer.is_file():
            raise BoostError("The Codex app didn't answer. Open it once and sign in, then try again.")
        return answer.read_text(encoding="utf-8")


def _ask_gemini_cli(prompt: str, timeout: int = 300) -> object:
    exe = find_app("gemini")
    if not exe:
        raise BoostError("The Gemini CLI isn't installed on this PC (npm install -g @google/gemini-cli).")
    ask = "Answer with only the JSON object described above, nothing else."
    with tempfile.TemporaryDirectory(prefix="clipstudio-ai-") as folder:
        for extra in (["--output-format", "json"], []):  # older Gemini CLIs have no --output-format
            try:
                run = subprocess.run([exe, "-p", ask, *extra], input=SYSTEM + "\n\n" + prompt, capture_output=True,
                                     text=True, encoding="utf-8", timeout=timeout, cwd=folder, creationflags=_no_window())
            except subprocess.TimeoutExpired as error:
                raise BoostError("The Gemini CLI took too long. The normal picker was used.") from error
            if run.returncode == 0 and run.stdout.strip():
                break
        else:
            raise BoostError("The Gemini CLI didn't answer. Run `gemini` once in a terminal and sign in, then try again.")
    try:
        envelope = json.loads(run.stdout)
    except ValueError:
        return run.stdout
    if isinstance(envelope, dict) and "response" in envelope:
        return envelope.get("response") or ""
    return envelope


def check_gemini_key(key: str | None) -> str:
    """Only a rough shape check: Google's own API is the authority on a key.

    Google now hands out two shapes and both work the same way: the classic “AIza…” keys, and the
    newer “AQ.Ab8…” keys that aistudio.google.com/app/apikey starts giving out. Earlier builds only
    accepted “AIza…”, so real new keys were refused before we ever called Google. Anything that is
    one long token of key characters is let through, so the next Google format won't need a fix.
    """
    key = (key or "").strip().strip('"').strip("'").strip()
    if key.startswith("sk-ant-"):
        raise BoostError(f"That's a Claude API key. Pick “Claude key” for it, or copy the Gemini key from {GEMINI_KEYS_URL}.")
    if len(key) < 20 or any(c.isspace() for c in key):
        raise BoostError(f"That doesn't look like a Gemini API key. Copy the whole key from {GEMINI_KEYS_URL} "
                         "(it starts with AIza or AQ).")
    return key


class _GeminiKeyRejected(BoostError):
    """Google refused the key itself, not the request: the other auth header is worth one try."""


def _gemini_auth_tries(key: str) -> list[dict]:
    """The two auth headers Google documents, most likely one first — and never a URL “?key=”.

    Classic “AIza…” API keys ride in the “x-goog-api-key” header; the newer “AQ…” authorization keys
    are bearer tokens. Whichever shape the key is gets its natural header first, and the other header
    is tried once if Google still says no, so both authentication flows work however Google wires a
    given key up. Keys stay out of URLs (“?key=” leaks into logs and history) and are never printed.
    """
    api_key = {"x-goog-api-key": key}
    bearer = {"Authorization": f"Bearer {key}"}
    return [api_key, bearer] if key.startswith("AIza") else [bearer, api_key]


def _ask_gemini_api(prompt: str, key: str, session=None) -> object:
    import requests

    session = session or requests
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM + " " + SHAPE}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    tries = _gemini_auth_tries(key)
    for count, headers in enumerate(tries):
        try:
            return _ask_gemini_models(body, headers, session)
        except _GeminiKeyRejected as error:
            if count == len(tries) - 1:  # both headers refused: it really is the key
                raise BoostError(f"Gemini didn't accept that key — it may have expired or been copied short. "
                                 f"Copy the whole key again from {GEMINI_KEYS_URL}.") from error
    raise AssertionError("unreachable")


def _ask_gemini_models(body: dict, headers: dict, session) -> object:
    import requests

    failed = ""
    deadline = time.monotonic() + GEMINI_TOTAL_SECONDS
    for model in GEMINI_MODELS:
        wait = min(GEMINI_TRY_SECONDS, deadline - time.monotonic())
        if wait < 5:
            break
        try:
            reply = session.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                 headers=headers, json=body, timeout=wait)
        except requests.Timeout:
            failed = "timed out"  # this model is queueing: a lighter one is worth a try
            continue
        except requests.RequestException as error:
            raise BoostError("Couldn't reach Gemini. Check your internet.") from error
        if reply.status_code in (401, 403) or (reply.status_code == 400 and "API key" in reply.text):
            raise _GeminiKeyRejected(f"Gemini didn't accept that key. Copy it again from {GEMINI_KEYS_URL}.")
        if reply.status_code == 429:
            raise BoostError("Gemini says slow down (free limit reached). The normal picker was used.")
        if reply.status_code >= 400:  # retired id (404) or a busy model (503): the next one usually answers
            failed = str(reply.status_code)
            continue
        try:
            parts = reply.json()["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise BoostError("Gemini's answer was empty. The normal picker was used.") from error
    if failed == "timed out" or not failed:
        raise BoostError("Gemini is queueing right now and took too long. The normal picker was used.")
    raise BoostError(f"Gemini had a problem ({failed}). The normal picker was used.")


LOCAL_MODEL = "qwen2.5:3b"  # ~2 GB, good at short JSON answers, runs on most PCs
_PULL = {"state": "idle", "error": ""}


def start_ollama() -> bool:
    """Ollama installed but not running (common after a reboot): start it quietly."""
    import requests

    try:
        requests.get(f"{OLLAMA}/api/tags", timeout=1.5)
        return True
    except Exception:
        pass
    exe = find_app("ollama")
    if not exe:
        return False
    try:
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_no_window())
    except OSError:
        return False
    import time

    for _ in range(20):
        time.sleep(0.5)
        try:
            requests.get(f"{OLLAMA}/api/tags", timeout=1)
            return True
        except Exception:
            continue
    return False


def pull_local_model():
    """One click: download the free local AI model in the background."""
    import threading

    import requests

    if _PULL["state"] == "pulling":
        return dict(_PULL)
    if not start_ollama():
        raise BoostError("Install Ollama first (free, ollama.com/download), then press this again.")

    def run():
        _PULL.update(state="pulling", error="")
        try:
            reply = requests.post(f"{OLLAMA}/api/pull", json={"model": LOCAL_MODEL, "stream": False}, timeout=3600)
            ok = reply.status_code < 400
            _PULL.update(state="ready" if ok else "error", error="" if ok else reply.text[:200])
        except Exception as error:  # noqa: BLE001
            _PULL.update(state="error", error=str(error)[:200])

    threading.Thread(target=run, name="ollama-pull", daemon=True).start()
    _PULL["state"] = "pulling"
    return dict(_PULL)


def pull_status() -> dict:
    return dict(_PULL)


def ollama_models(session=None) -> list[str]:
    """Models in Ollama on this PC (free, runs on your own GPU/CPU, no key), or []."""
    import requests

    if session is None:
        start_ollama()
    try:
        reply = (session or requests).get(f"{OLLAMA}/api/tags", timeout=2)
        return [m["name"] for m in reply.json().get("models", []) if m.get("name")]
    except Exception:
        return []


def _ask_ollama(prompt: str, session=None) -> object:
    import requests

    models = ollama_models(session)
    if not models:
        raise BoostError("Local AI has no model yet. Press \"Get the free AI model\" in AI boost (about 2 GB, once).")
    # Prefer a small fast model when several are installed.
    model = next((m for pref in ("llama3.2", "qwen2.5", "gemma", "mistral", "llama3") for m in models if m.startswith(pref)), models[0])
    try:
        reply = (session or requests).post(f"{OLLAMA}/api/generate", timeout=300, json={
            "model": model, "system": SYSTEM, "format": "json",
            "prompt": prompt + '\n\nAnswer exactly like: {"moments": [{"id": "m1", "score": 8, "title": "short funny title"}]}', "stream": False,
            "options": {"temperature": 0.2}})
        return reply.json().get("response") or ""
    except Exception as error:
        raise BoostError("Ollama didn't answer in time. The normal picker was used.") from error


def _safe_find(name: str) -> str:
    try:
        return find_app(name) or ""
    except Exception:  # noqa: BLE001
        return ""


def _has(name: str) -> bool:
    """Never let one odd folder break the whole AI box."""
    try:
        return bool(find_app(name))
    except Exception:  # noqa: BLE001
        return False


CUSTOM_PATHS: dict[str, str] = {}  # app name -> path the buyer pasted (loaded by Booster)
_PATHS_FILE: list = [None]


def real_path(path, _depth: int = 0) -> Path:
    """Follow junctions/symlinks by reading their targets, never by walking through them.
    Codex's bin is a junction to a junction; Windows refuses (WinError 448, 'untrusted mount point')
    to let some apps walk through user-made junctions, but reading the target is allowed."""
    parts = Path(path).parts
    if not parts:
        return Path(path)
    current = Path(parts[0])
    for part in parts[1:]:
        current = current / part
        for _ in range(8):
            try:
                target = os.readlink(current)
            except (OSError, ValueError, NotImplementedError):
                break
            if target.startswith("\\\\?\\"):
                target = target[4:]
            current = Path(target) if os.path.isabs(target) else current.parent / target
            if _depth < 8:
                current = real_path(current, _depth + 1)  # the target can hold junctions too
    return current


def _file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        # WinError 448 ('untrusted mount point') on junction folders: pathlib's check can refuse
        # while the raw OS check still answers, and that answer is the true one.
        return os.path.isfile(path)


def find_app(name: str) -> str | None:
    """A command-line app like a terminal would find it, plus the usual install folders (npm global,
    Claude/Codex/Ollama installers). An app opened from the Start menu can miss new PATH entries.
    Returns the real file path (junctions resolved), so it can also be started."""
    custom = CUSTOM_PATHS.get(name)
    if custom:
        resolved = real_path(custom)
        if _file(resolved):
            return str(resolved)
    try:
        hit = shutil.which(name)
    except OSError:
        hit = None
    if hit:
        resolved = real_path(hit)
        return str(resolved) if _file(resolved) else hit
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    folders = [appdata / "npm", home / ".local" / "bin", home / ".claude" / "local", home / "scoop" / "shims",
               local / "Programs" / "Ollama", local / "Programs" / "claude", local / "Microsoft" / "WinGet" / "Links",
               Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs", home / ".bun" / "bin",
               home / ".codex" / "bin", local / "pnpm", local / "Programs" / "OpenAI" / "Codex" / "bin"]
    folders += [local / "Microsoft" / "WindowsApps", home / ".volta" / "bin", appdata / "fnm" / "aliases" / "default",
                Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "nodejs",
                Path(os.environ.get("NVM_SYMLINK") or r"C:\nvm4w\nodejs")]
    for nvm in (Path(os.environ.get("NVM_HOME") or appdata / "nvm"),):
        try:
            folders += sorted((d for d in nvm.iterdir() if d.is_dir()), reverse=True)
        except OSError:
            pass
    try:
        # PATH as set now (user + whole PC), read fresh: the app may have started before an install
        import winreg

        for hive, sub in ((winreg.HKEY_CURRENT_USER, "Environment"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
            try:
                with winreg.OpenKey(hive, sub) as key:
                    folders += [Path(os.path.expandvars(p)) for p in str(winreg.QueryValueEx(key, "Path")[0]).split(";") if p]
            except OSError:
                continue
    except Exception:
        pass
    if name == "codex":
        # Codex's own installer: ~/.codex/packages/standalone/releases/<version>/bin (newest first)
        try:
            releases = home / ".codex" / "packages" / "standalone" / "releases"
            folders += [d / "bin" for d in sorted(releases.iterdir(), reverse=True) if d.is_dir()]
        except OSError:
            pass
    for folder in folders:
        try:
            folder = real_path(folder)
        except Exception:  # noqa: BLE001
            continue
        for ext in (".exe", ".cmd", ".bat", ""):
            candidate = folder / f"{name}{ext}"
            if _file(candidate):
                return str(candidate)
    return None


def set_custom_path(name: str, path: str) -> str:
    """The buyer pastes where the app is (or the folder it's in). Checked, then remembered."""
    if name not in ("claude", "codex", "gemini", "ollama"):
        raise BoostError("Pick Claude, Codex, Gemini or Ollama.")
    raw = Path(str(path).strip().strip('"').strip("'"))
    if not str(raw).strip() or str(raw) == ".":
        CUSTOM_PATHS.pop(name, None)
    else:
        found = None
        for candidate in ([raw] if raw.suffix else []) + [raw / f"{name}{ext}" for ext in (".exe", ".cmd", ".bat")]:
            resolved = real_path(candidate)
            if _file(resolved):
                found = resolved
                break
        if not found:
            raise BoostError(f"No {name} program at that place. Paste the full path to {name}.exe (or .cmd), "
                             f"e.g. what “where {name}” prints in a Command Prompt.")
        CUSTOM_PATHS[name] = str(found)
    if _PATHS_FILE[0]:
        try:
            Path(_PATHS_FILE[0]).write_text(json.dumps(CUSTOM_PATHS), encoding="utf-8")
        except OSError:
            pass
    return CUSTOM_PATHS.get(name, "")


# ---------------- settings + entry points ----------------
class Booster:
    def __init__(self, root: Path, api_client=None):
        self.path = Path(root) / "ai.json"
        self.api_client = api_client
        _PATHS_FILE[0] = Path(root) / "ai-paths.json"
        try:
            CUSTOM_PATHS.update(json.loads(_PATHS_FILE[0].read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass

    def settings(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        mode = data.get("mode") if data.get("mode") in MODES else "off"
        return {"mode": mode}

    def status(self) -> dict:
        key = vault.get(KEY_NAME)
        gkey = vault.get(GEMINI_KEY_NAME)
        try:
            models = ollama_models()
        except Exception:  # noqa: BLE001
            models = []
        return {
            **self.settings(),
            "has_key": bool(key),
            "key_hint": vault.hint(key),
            "has_gemini_key": bool(gkey),
            "gemini_hint": vault.hint(gkey),
            "installed": {**{name: _has(name) for name in ("claude", "codex", "gemini")},
                          "local": _has("ollama") or bool(models)},
            "local_models": models,
            "paths": {name: _safe_find(name) for name in ("claude", "codex", "gemini")},
        }

    def save(self, mode: str, key: str | None = None) -> dict:
        if mode not in MODES:
            raise BoostError("Pick Off, Claude, Codex, Gemini or Local AI.")
        if key:
            key = key.strip()
            if mode == "gemini_api":
                vault.put(GEMINI_KEY_NAME, check_gemini_key(key))
            else:
                if not key.startswith("sk-ant-") or len(key) < 30:
                    raise BoostError("That doesn't look like a Claude API key (it starts with sk-ant-).")
                vault.put(KEY_NAME, key)
        if mode == "api" and not vault.get(KEY_NAME):
            raise BoostError("Paste your Claude API key first.")
        if mode == "gemini_api" and not vault.get(GEMINI_KEY_NAME):
            raise BoostError("Paste your Gemini API key first.")
        self.path.write_text(json.dumps({"mode": mode}), encoding="utf-8")
        return self.status()

    def forget_key(self):
        vault.delete(KEY_NAME)
        vault.delete(GEMINI_KEY_NAME)
        if self.settings()["mode"] in ("api", "gemini_api"):
            self.path.write_text(json.dumps({"mode": "off"}), encoding="utf-8")

    def ask(self, prompt: str) -> object:
        mode = self.settings()["mode"]
        if mode == "api":
            key = vault.get(KEY_NAME)
            if not key:
                raise BoostError("Paste your Claude API key in AI boost.")
            return _ask_api(prompt, key, self.api_client)
        if mode == "claude":
            return _ask_claude_cli(prompt)
        if mode == "codex":
            return _ask_codex_cli(prompt)
        if mode == "gemini":
            return _ask_gemini_cli(prompt)
        if mode == "local":
            return _ask_ollama(prompt)
        if mode == "gemini_api":
            key = vault.get(GEMINI_KEY_NAME)
            if not key:
                raise BoostError("Paste your Gemini API key in AI boost.")
            return _ask_gemini_api(prompt, key)
        raise BoostError("AI boost is off.")

    @property
    def on(self) -> bool:
        return self.settings()["mode"] != "off"

    def rate(self, moments: list[dict], streamer: str = "", topic: str = "") -> dict[str, Pick]:
        """moments: [{id, start, end, score, text}] -> {id: Pick}"""
        if not moments:
            return {}
        answer = self.ask(build_prompt(moments, streamer, topic))
        return {p.id: p for p in parse_picks(answer, {m["id"] for m in moments})}

    def test(self) -> str:
        sample = [{"id": "a", "start": 0, "end": 20, "score": 0.8,
                   "text": "bro I told you not to jump, why would you jump off the roof, he jumped off the roof"}]
        picks = self.rate(sample)
        return picks["a"].title


def blend(moments: list[dict], picks: dict[str, Pick], keep: int) -> list[dict]:
    """Best `keep` moments: mostly the AI's score, a little of the loudness/motion score.
    A moment with almost no words was judged blind by the AI: there the loudness/motion score leads."""
    def value(m):
        pick = picks.get(m["id"])
        energy = m.get("score", 0)
        if len(str(m.get("text") or "").split()) < 4:
            return energy * 0.5 + (pick.score / 10 * 0.15 if pick else 0)
        return (pick.score / 10 * 0.8 + energy * 0.2) if pick else energy * 0.2
    return sorted(moments, key=value, reverse=True)[:keep]

