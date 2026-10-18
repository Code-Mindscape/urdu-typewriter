"""
test_roman_urdu.py — headless check of Roman Urdu -> Urdu script
transliteration: the dictionary has no duplicate keys mapping to
different values (a silent-overwrite bug class that's easy to introduce
when adding new entries), previously-wrong outputs are now right, and
previously-right output stays right.

    python test_roman_urdu.py
"""

import sys
import ast

import roman_urdu as ru

results = []


def check(name, cond, detail=""):
    status = "OK" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    results.append(cond)


def check_eq(name, got, expected):
    check(name, got == expected, f"got {got!r}, expected {expected!r}")


# ----------------------------------------------------------------------
# No key in COMMON_WORDS (across the base dict and its .update() calls)
# silently maps to two different Urdu spellings -- whichever definition
# happens to run last would win with no warning. Parsing the source
# (rather than just inspecting the loaded dict) lets this catch the bug
# even when it's a later block overwriting an earlier one with the
# *same* dict object, which a runtime dict has no memory of afterwards.
# ----------------------------------------------------------------------
print("--- dictionary integrity ---")
tree = ast.parse(open("roman_urdu.py", encoding="utf-8").read())
blocks = []
for node in ast.walk(tree):
    dict_node = None
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
        if (
            len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "COMMON_WORDS"
        ):
            dict_node = node.value
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "update"
    ):
        if (
            isinstance(node.func.value, ast.Name)
            and node.func.value.id == "COMMON_WORDS"
            and node.args
            and isinstance(node.args[0], ast.Dict)
        ):
            dict_node = node.args[0]
    if dict_node is not None:
        d = {}
        for k, v in zip(dict_node.keys, dict_node.values):
            if isinstance(k, ast.Constant):
                d[k.value] = v.value
        blocks.append(d)

seen = {}
conflicts = {}
for block in blocks:
    for k, v in block.items():
        if k in seen and seen[k] != v:
            conflicts[k] = (seen[k], v)
        seen[k] = v
check(
    "no COMMON_WORDS key silently maps to two different spellings",
    len(conflicts) == 0,
    conflicts,
)
check(
    "dictionary has meaningfully grown (900+ entries)",
    ru.dictionary_size() >= 900,
    f"{ru.dictionary_size()} entries",
)

# ----------------------------------------------------------------------
# Specific bug fixes
# ----------------------------------------------------------------------
print("\n--- specific fixes ---")
check_eq(
    "'walaikum' (was transposed: ل/ع swapped)", ru.transliterate("walaikum"), "وعلیکم"
)
check_eq("'walaikumsalam'", ru.transliterate("walaikumsalam"), "وعلیکم السلام")

# Mid-word "an"/"in"/"un"/"en"/"on" should NOT be read as word-final
# nasalization (that turned "mangal" into "ماں" (mother) + garbage) --
# now in the dictionary directly, so this is a guaranteed exact match,
# not just "no longer obviously wrong".
check_eq(
    "'mangal' (Tuesday) no longer misparsed as 'ماں' + garbage",
    ru.transliterate("mangal"),
    "منگل",
)

# Word-final nasalization should still work correctly for phonetic
# fallback (not just dictionary hits) -- "zaqtan" is a synthetic,
# non-word chosen specifically because it has no dictionary entry and no
# close fuzzy-match neighbor, isolating the phonetic path.
check(
    "'an'-style word-final phonetic fallback still nasalizes when "
    "genuinely at the end of a word",
    not ru.is_in_dictionary("zaqtan") and ru.transliterate("zaqtan").endswith("اں"),
    ru.transliterate("zaqtan"),
)

check_eq(
    "hyphenated dictionary entry now reachable (word regex allows "
    "hyphens in the matched core)",
    ru.transliterate("jazakallah-khair"),
    "جزاک اللہ خیر",
)
check_eq("another hyphenated entry", ru.transliterate("in-sha-allah"), "ان شاء اللہ")

# "taiba" (a name) was being silently "corrected" by the fuzzy dictionary
# matcher into "taliba" ("female student") -- a different word that just
# happens to be textually close. Fixed two ways: "taiba" is now its own
# dictionary entry (so it never reaches the fuzzy matcher at all), and
# the fuzzy matcher itself now refuses a candidate whose consonants
# aren't exactly the ones the typed word had -- see _fuzzy_dictionary_match.
check_eq(
    "'taiba' (name) no longer misfires as 'taliba' (student)",
    ru.transliterate("taiba"),
    "طیبہ",
)
check_eq("'TAIBA' uppercase", ru.transliterate("TAIBA"), "طیبہ")
check_eq("'tayyaba' spelling variant", ru.transliterate("tayyaba"), "طیبہ")
check(
    "fuzzy matcher rejects a same-length-ish but consonant-different "
    "word ('taiba' must not become 'taliba')",
    ru._fuzzy_dictionary_match("taiba") != "taliba",
    ru._fuzzy_dictionary_match("taiba"),
)
check_eq(
    "fuzzy matcher still fixes a genuine vowel-only near-miss "
    "('mohabt' -> 'mohabbat', same consonants either way)",
    ru.transliterate("mohabt"),
    "محبت",
)

# ----------------------------------------------------------------------
# Newly-added vocabulary (spot check across categories)
# ----------------------------------------------------------------------
print("\n--- newly added vocabulary ---")
new_words = {
    "peer": "پیر",
    "budh": "بدھ",
    "jumeraat": "جمعرات",
    "itwar": "اتوار",
    "january": "جنوری",
    "december": "دسمبر",
    "video": "ویڈیو",
    "password": "پاس ورڈ",
    "download": "ڈاؤن لوڈ",
    "website": "ویب سائٹ",
    "wifi": "وائی فائی",
    "doctor": "ڈاکٹر",
    "hospital": "ہسپتال",
    "university": "یونیورسٹی",
    "gaari": "گاڑی",
    "rickshaw": "رکشہ",
    "khushi": "خوشی",
    "pareshan": "پریشان",
    "matlab": "مطلب",
    "khair": "خیر",
    "pasand": "پسند",
    "mumkin": "ممکن",
}
for roman, expected in new_words.items():
    check_eq(f"'{roman}'", ru.transliterate(roman), expected)

# ----------------------------------------------------------------------
# Regression: previously-correct behavior stays correct
# ----------------------------------------------------------------------
print("\n--- regression: existing behavior unaffected ---")
check_eq("dictionary word 'mein'", ru.transliterate("mein"), "میں")
check_eq(
    "dictionary word 'yahan' (word-final nasal, unaffected by the "
    "mid-word nasal fix since it's a dictionary hit)",
    ru.transliterate("yahan"),
    "یہاں",
)
check_eq(
    "full sentence, all dictionary words",
    ru.transliterate("main aap se pyar karta hun"),
    "میں آپ سے پیار کرتا ہوں",
)
check_eq(
    "full sentence 2", ru.transliterate("zindagi khubsurat hai"), "زندگی خوبصورت ہے"
)
check_eq(
    "ordinary trailing punctuation still separated from the word",
    ru.transliterate("kya?"),
    "کیا؟",
)
check_eq(
    "ordinary surrounding punctuation still separated from the word",
    ru.transliterate("acha,"),
    "اچھا،",
)
check_eq(
    "parentheses still pass through around the word",
    ru.transliterate("(gaya)"),
    "(گیا)",
)
check_eq("digits pass through unchanged", ru.transliterate("123"), "123")
check_eq("empty string", ru.transliterate(""), "")
check_eq("whitespace preserved exactly", ru.transliterate("kya  hai"), "کیا  ہے")

print()
if all(results):
    print(f"ALL {len(results)} ROMAN URDU CHECKS PASSED")
else:
    print(f"{results.count(False)} of {len(results)} CHECKS FAILED")
    sys.exit(1)
