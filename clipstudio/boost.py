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
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import vault

MODES = ("off", "api", "claude", "codex", "gemini", "gemini_api", "local")
OLLAMA = "http://127.0.0.1:11434"
KEY_NAME = "anthropic_api_key"
GEMINI_KEY_NAME = "gemini_api_key"
MODEL = "claude-opus-5-5"
GEMINI_MODEL = "gemini-flash-latest"
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


def parse_picks(data, ids: set[str]) -> list[Pick]:
    if isinstance(data, str):
        start, end = data.find("{"), data.rfind("}")
        if start < 0 or end <= start:
            raise BoostError("The AI didn't send back a list of moments.")
        try:
            data = json.loads(data[start : end + 1])
        except ValueError as error:
            raise BoostError("The AI's answer wasn't readable.") from error
    picks = []
    for row in (data or {}).get("moments", []):
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
    exe = shutil.which("claude")
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
    exe = shutil.which("codex")
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
    exe = shutil.which("gemini")
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


def _ask_gemini_api(prompt: str, key: str, session=None) -> object:
    import requests

    session = session or requests
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    try:
        reply = session.post(f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
                             headers={"x-goog-api-key": key}, json=body, timeout=120)
    except requests.RequestException as error:
        raise BoostError("Couldn't reach Gemini. Check your internet.") from error
    if reply.status_code in (401, 403) or (reply.status_code == 400 and "API key" in reply.text):
        raise BoostError("Gemini didn't accept that API key.")
    if reply.status_code == 429:
        raise BoostError("Gemini says slow down (free limit reached). The normal picker was used.")
    if reply.status_code >= 400:
        raise BoostError(f"Gemini had a problem ({reply.status_code}). The normal picker was used.")
    try:
        parts = reply.json()["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise BoostError("Gemini's answer was empty. The normal picker was used.") from error


def ollama_models(session=None) -> list[str]:
    """Models in Ollama on this PC (free, runs on your own GPU/CPU, no key), or []."""
    import requests

    try:
        reply = (session or requests).get(f"{OLLAMA}/api/tags", timeout=2)
        return [m["name"] for m in reply.json().get("models", []) if m.get("name")]
    except Exception:
        return []


def _ask_ollama(prompt: str, session=None) -> object:
    import requests

    models = ollama_models(session)
    if not models:
        raise BoostError("Ollama isn't running or has no model. Install it from ollama.com, then run `ollama pull llama3.2`.")
    # Prefer a small fast model when several are installed.
    model = next((m for pref in ("llama3.2", "qwen2.5", "gemma", "mistral", "llama3") for m in models if m.startswith(pref)), models[0])
    try:
        reply = (session or requests).post(f"{OLLAMA}/api/generate", timeout=300, json={
            "model": model, "system": SYSTEM, "prompt": prompt, "format": "json", "stream": False,
            "options": {"temperature": 0.2}})
        return reply.json().get("response") or ""
    except Exception as error:
        raise BoostError("Ollama didn't answer in time. The normal picker was used.") from error


# ---------------- settings + entry points ----------------
class Booster:
    def __init__(self, root: Path, api_client=None):
        self.path = Path(root) / "ai.json"
        self.api_client = api_client

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
        return {
            **self.settings(),
            "has_key": bool(key),
            "key_hint": vault.hint(key),
            "has_gemini_key": bool(gkey),
            "gemini_hint": vault.hint(gkey),
            "installed": {**{name: bool(shutil.which(name)) for name in ("claude", "codex", "gemini")},
                          "local": bool(shutil.which("ollama")) or bool(ollama_models())},
        }

    def save(self, mode: str, key: str | None = None) -> dict:
        if mode not in MODES:
            raise BoostError("Pick Off, Claude, Codex, Gemini or Local AI.")
        if key:
            key = key.strip()
            if mode == "gemini_api":
                if not key.startswith("AIza") or len(key) < 30:
                    raise BoostError("That doesn't look like a Gemini API key (it starts with AIza).")
                vault.put(GEMINI_KEY_NAME, key)
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

