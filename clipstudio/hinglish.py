"""Hindi (Devanagari) -> Hinglish (Hindi written in English letters, the way people type it).

No model, no download: common words come from a small table, everything else from letter rules
with the usual "drop the silent a" rule (karna, samajhna, dekh, not karanaa / dekha)."""

from __future__ import annotations

import re

_WORDS = {
    "है": "hai", "मैंने": "maine", "तूने": "tune", "उसने": "usne", "किसने": "kisne", "हैं": "hain", "में": "mein", "मैं": "main", "नहीं": "nahi", "नही": "nahi",
    "क्या": "kya", "हूँ": "hoon", "हूं": "hoon", "यह": "yeh", "वह": "woh", "ये": "ye", "वो": "wo",
    "और": "aur", "भी": "bhi", "तो": "toh", "कि": "ki", "क्यों": "kyun", "क्यूँ": "kyun", "भाई": "bhai",
    "अरे": "arey", "चल": "chal", "चलो": "chalo", "देख": "dekh", "देखो": "dekho", "बहुत": "bahut",
    "अब": "ab", "यार": "yaar", "पर": "par", "से": "se", "को": "ko", "का": "ka", "की": "ki", "के": "ke",
    "था": "tha", "थी": "thi", "थे": "the", "रहा": "raha", "रही": "rahi", "रहे": "rahe", "कर": "kar",
    "हो": "ho", "गया": "gaya", "गई": "gayi", "गए": "gaye", "मत": "mat", "एक": "ek", "सब": "sab",
    "कुछ": "kuch", "हम": "hum", "तुम": "tum", "आप": "aap", "मुझे": "mujhe", "तुझे": "tujhe",
    "उसे": "use", "इसे": "ise", "अच्छा": "accha", "ठीक": "theek", "पता": "pata", "लिए": "liye",
    "मेरा": "mera", "मेरी": "meri", "मेरे": "mere", "तेरा": "tera", "तेरी": "teri", "तेरे": "tere",
    "इधर": "idhar", "उधर": "udhar", "कहाँ": "kahan", "कहां": "kahan", "यहाँ": "yahan", "यहां": "yahan",
    "वहाँ": "wahan", "वहां": "wahan", "जल्दी": "jaldi", "पैसे": "paise", "गाड़ी": "gaadi", "पुलिस": "police",
    "ओके": "okay", "हाँ": "haan", "हां": "haan", "ना": "na", "बस": "bas", "वाला": "wala", "वाली": "wali",
    "वाले": "wale", "क्योंकि": "kyunki", "लेकिन": "lekin", "मार": "maar", "मारो": "maaro", "भागो": "bhaago",
    "दे": "de", "दो": "do", "ले": "le", "लो": "lo", "आजा": "aaja", "जा": "ja", "जाओ": "jao",
}

_CONS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh",
    "ञ": "n", "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d",
    "ध": "dh", "न": "n", "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r",
    "ल": "l", "व": "v", "श": "sh", "ष": "sh", "स": "s", "ह": "h", "ळ": "l",
    "क़": "q", "ख़": "kh", "ग़": "g", "ज़": "z", "फ़": "f", "ड़": "d", "ढ़": "rh", "य़": "y",
}
_NUKTA = {"क": "क़", "ख": "ख़", "ग": "ग़", "ज": "ज़", "फ": "फ़", "ड": "ड़", "ढ": "ढ़", "य": "य़"}
_VOWELS = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ए": "e", "ऐ": "ai",
           "ओ": "o", "औ": "au", "ऋ": "ri", "ऑ": "o", "ॲ": "e"}
_SIGNS = {"ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "oo", "े": "e", "ै": "ai", "ो": "o",
          "ौ": "au", "ृ": "ri", "ॉ": "o", "ॅ": "e"}
_DIGITS = {chr(0x0966 + i): str(i) for i in range(10)}
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def has_devanagari(text: str) -> bool:
    return bool(_DEVANAGARI.search(text or ""))


def _word(word: str) -> str:
    if word in _WORDS:
        return _WORDS[word]
    # Units: [kind, latin, vowel, implicit]; kind "c" consonant / "v" vowel / "x" other.
    units: list[list] = []
    chars = list(word)
    i = 0
    while i < len(chars):
        ch = chars[i]
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if ch in _CONS or ch in _NUKTA:
            if nxt == "़" and ch in _NUKTA:
                ch = _NUKTA[ch]
                i += 1
                nxt = chars[i + 1] if i + 1 < len(chars) else ""
            units.append(["c", _CONS.get(ch, ""), "a", True])
        elif ch == "़":
            pass
        elif ch in _SIGNS and units and units[-1][0] == "c":
            units[-1][2], units[-1][3] = _SIGNS[ch], False
        elif ch == "्" and units and units[-1][0] == "c":
            units[-1][2], units[-1][3] = "", False
        elif ch in _VOWELS:
            units.append(["v", _VOWELS[ch], "", False])
        elif ch in "ंँ":
            following = chars[i + 1] if i + 1 < len(chars) else ""
            nasal = "m" if following in ("प", "फ", "ब", "भ", "म") else "n"
            units.append(["x", nasal, "", False])
        elif ch == "ः":
            units.append(["x", "h", "", False])
        elif ch in _DIGITS:
            units.append(["x", _DIGITS[ch], "", False])
        else:
            units.append(["x", ch, "", False])
        i += 1
    cons = [k for k, u in enumerate(units) if u[0] == "c"]
    # Silent final "a" (words longer than one letter).
    letters = [k for k, u in enumerate(units) if u[0] in ("c", "v")]
    if len(letters) > 1:
        last = letters[-1]
        if units[last][0] == "c" and units[last][3]:
            units[last][2] = ""
    # Silent middle "a": V C(a) C V -> drop, right to left.
    for k in reversed(cons):
        u = units[k]
        if not u[3] or not u[2]:
            continue
        before = units[k - 1] if k > 0 else None
        after = units[k + 1] if k + 1 < len(units) else None
        voiced_before = before is not None and (before[0] == "v" or (before[0] == "c" and before[2]))
        voiced_after = after is not None and after[0] == "c" and bool(after[2])
        if voiced_before and voiced_after:
            u[2] = ""
    out = []
    for kind, latin, vowel, _ in units:
        out.append(latin + vowel if kind == "c" else latin)
    text = "".join(out)
    if text.endswith("ein") and word.endswith("ें"):
        pass
    elif word.endswith("ें"):
        text = text[:-2] + "ein"
    return text


def romanize(text: str) -> str:
    """Devanagari words to Hinglish; anything else (English words, emoji, numbers) stays."""
    if not has_devanagari(text):
        return text
    parts = re.split(r"([ऀ-ॣ०-ॿ]+)", text)
    return "".join(_word(p) if has_devanagari(p) else p.replace("।", ".").replace("॥", ".") for p in parts)


def romanize_segments(segments: list[dict]) -> list[dict]:
    out = []
    for seg in segments or []:
        item = dict(seg)
        item["text"] = romanize(str(seg.get("text", "")))
        if seg.get("words"):
            item["words"] = [dict(w, text=romanize(str(w.get("text", "")))) for w in seg["words"]]
        out.append(item)
    return out
