"""Language names used in file-name tags, mapped to the codes TMDB uses.

TMDB's `original_language` is an ISO 639-1 code, plus a few house codes:
"cn" (Cantonese), "xx" (no language), "sh" (Serbo-Croatian). A tag is only a
hint: "Chinese" can be "zh" (Mandarin) or "cn" (Cantonese), a Galician film may
be filed under "es", and so on, so several names map to more than one code.
"""

from __future__ import annotations

LANGUAGE_CODES: dict[str, tuple[str, ...]] = {
    "afrikaans": ("af",), "albanian": ("sq",), "amharic": ("am",), "arabic": ("ar",),
    "aramaic": ("ar", "he"), "armenian": ("hy",), "assamese": ("as",), "azerbaijani": ("az",),
    "bambara": ("bm",), "bangla": ("bn",), "basque": ("eu", "es"), "belarusian": ("be",),
    "bengali": ("bn",), "bosnian": ("bs", "sh", "hr", "sr"), "breton": ("br", "fr"),
    "bulgarian": ("bg",), "burmese": ("my",), "cantonese": ("cn", "zh"), "castilian": ("es",),
    "catalan": ("ca", "es"), "chechen": ("ce",), "chinese": ("zh", "cn"), "corsican": ("co", "fr"),
    "croatian": ("hr", "sh", "sr", "bs"), "czech": ("cs",), "danish": ("da",), "dari": ("fa", "ps"),
    "dutch": ("nl",), "dzongkha": ("dz",), "english": ("en",), "esperanto": ("eo",),
    "estonian": ("et",), "faroese": ("fo",), "farsi": ("fa",), "filipino": ("tl",),
    "finnish": ("fi",), "flemish": ("nl",), "french": ("fr",), "frisian": ("fy", "nl"),
    "fula": ("ff",), "gaelic": ("ga", "gd"), "galician": ("gl", "es"), "georgian": ("ka",),
    "german": ("de",), "greek": ("el",), "greenlandic": ("kl", "da"), "guarani": ("gn", "es"),
    "gujarati": ("gu",), "haitian": ("ht", "fr"), "haitian creole": ("ht", "fr"), "hausa": ("ha",),
    "hebrew": ("he",), "hindi": ("hi", "ur"), "hindustani": ("hi", "ur"), "hokkien": ("zh",),
    "hungarian": ("hu",), "icelandic": ("is",), "igbo": ("ig",), "indonesian": ("id",),
    "inuktitut": ("iu",), "irish": ("ga",), "italian": ("it",), "japanese": ("ja",),
    "javanese": ("jv",), "kannada": ("kn",), "kashmiri": ("ks",), "kazakh": ("kk",),
    "khmer": ("km",), "kinyarwanda": ("rw",), "korean": ("ko",), "kurdish": ("ku",),
    "kyrgyz": ("ky",), "lao": ("lo",), "latin": ("la",), "latvian": ("lv",), "lingala": ("ln",),
    "lithuanian": ("lt",), "luxembourgish": ("lb",), "macedonian": ("mk",), "malagasy": ("mg",),
    "malay": ("ms",), "malayalam": ("ml",), "maltese": ("mt",), "mandarin": ("zh",),
    "maori": ("mi",), "marathi": ("mr",), "moldovan": ("ro",), "mongolian": ("mn",),
    "montenegrin": ("sr", "sh"), "nepali": ("ne",), "norwegian": ("no", "nb", "nn"),
    "occitan": ("oc", "fr"), "odia": ("or",), "oriya": ("or",), "pashto": ("ps",),
    "persian": ("fa",), "polish": ("pl",), "portuguese": ("pt",), "punjabi": ("pa",),
    "quechua": ("qu", "es"), "romani": ("ro",), "romanian": ("ro",), "romansh": ("rm", "de"),
    "russian": ("ru",), "sami": ("se",), "samoan": ("sm",), "sanskrit": ("sa",),
    "scottish gaelic": ("gd",), "serbian": ("sr", "sh", "hr", "bs"),
    "serbo-croatian": ("sh", "sr", "hr", "bs"), "shanghainese": ("zh", "cn"), "sindhi": ("sd",),
    "sinhala": ("si",), "sinhalese": ("si",), "slovak": ("sk",), "slovene": ("sl",),
    "slovenian": ("sl",), "somali": ("so",), "spanish": ("es",), "sundanese": ("su",),
    "swahili": ("sw",), "swedish": ("sv",), "swiss german": ("de",), "tagalog": ("tl",),
    "taiwanese": ("zh",), "tajik": ("tg",), "tamil": ("ta",), "tatar": ("tt",), "telugu": ("te",),
    "thai": ("th",), "tibetan": ("bo",), "tigrinya": ("ti",), "tswana": ("tn",), "turkish": ("tr",),
    "turkmen": ("tk",), "twi": ("tw", "ak"), "ukrainian": ("uk",), "urdu": ("ur", "hi"),
    "uyghur": ("ug",), "uzbek": ("uz",), "vietnamese": ("vi",), "welsh": ("cy",), "wolof": ("wo",),
    "xhosa": ("xh",), "yiddish": ("yi",), "yoruba": ("yo",), "zulu": ("zu",),
    "silent": ("xx",), "no dialogue": ("xx",), "no language": ("xx",), "wordless": ("xx",),
}

# Display names for TMDB codes (what the map shows).
CODE_NAMES: dict[str, str] = {
    "af": "Afrikaans", "ak": "Akan", "am": "Amharic", "ar": "Arabic", "as": "Assamese",
    "az": "Azerbaijani", "be": "Belarusian", "bg": "Bulgarian", "bm": "Bambara", "bn": "Bengali",
    "bo": "Tibetan", "br": "Breton", "bs": "Bosnian", "ca": "Catalan", "ce": "Chechen",
    "cn": "Cantonese", "co": "Corsican", "cs": "Czech", "cy": "Welsh", "da": "Danish",
    "de": "German", "dz": "Dzongkha", "el": "Greek", "en": "English", "eo": "Esperanto",
    "es": "Spanish", "et": "Estonian", "eu": "Basque", "fa": "Persian", "ff": "Fula",
    "fi": "Finnish", "fo": "Faroese", "fr": "French", "fy": "Frisian", "ga": "Irish",
    "gd": "Scottish Gaelic", "gl": "Galician", "gn": "Guarani", "gu": "Gujarati", "ha": "Hausa",
    "he": "Hebrew", "hi": "Hindi", "hr": "Croatian", "ht": "Haitian Creole", "hu": "Hungarian",
    "hy": "Armenian", "id": "Indonesian", "ig": "Igbo", "is": "Icelandic", "it": "Italian",
    "iu": "Inuktitut", "ja": "Japanese", "jv": "Javanese", "ka": "Georgian", "kk": "Kazakh",
    "kl": "Greenlandic", "km": "Khmer", "kn": "Kannada", "ko": "Korean", "ks": "Kashmiri",
    "ku": "Kurdish", "ky": "Kyrgyz", "la": "Latin", "lb": "Luxembourgish", "ln": "Lingala",
    "lo": "Lao", "lt": "Lithuanian", "lv": "Latvian", "mg": "Malagasy", "mi": "Maori",
    "mk": "Macedonian", "ml": "Malayalam", "mn": "Mongolian", "mr": "Marathi", "ms": "Malay",
    "mt": "Maltese", "my": "Burmese", "nb": "Norwegian", "ne": "Nepali", "nl": "Dutch",
    "nn": "Norwegian", "no": "Norwegian", "oc": "Occitan", "or": "Odia", "pa": "Punjabi",
    "pl": "Polish", "ps": "Pashto", "pt": "Portuguese", "qu": "Quechua", "rm": "Romansh",
    "ro": "Romanian", "ru": "Russian", "rw": "Kinyarwanda", "sa": "Sanskrit", "sd": "Sindhi",
    "se": "Sami", "sh": "Serbo-Croatian", "si": "Sinhala", "sk": "Slovak", "sl": "Slovenian",
    "sm": "Samoan", "so": "Somali", "sq": "Albanian", "sr": "Serbian", "su": "Sundanese",
    "sv": "Swedish", "sw": "Swahili", "ta": "Tamil", "te": "Telugu", "tg": "Tajik", "th": "Thai",
    "ti": "Tigrinya", "tk": "Turkmen", "tl": "Tagalog", "tn": "Tswana", "tr": "Turkish",
    "tt": "Tatar", "tw": "Twi", "ug": "Uyghur", "uk": "Ukrainian", "ur": "Urdu", "uz": "Uzbek",
    "vi": "Vietnamese", "wo": "Wolof", "xh": "Xhosa", "xx": "No dialogue", "yi": "Yiddish",
    "yo": "Yoruba", "zh": "Mandarin", "zu": "Zulu",
}


def codes_for(name: str) -> tuple[str, ...]:
    """TMDB codes for a language name from a file-name tag ('' if unknown)."""
    return LANGUAGE_CODES.get(" ".join(name.casefold().split()), ())


def display_name(code: str | None) -> str:
    if not code:
        return "Unknown"
    return CODE_NAMES.get(code, code.upper())


def extend_from_tmdb(languages: list[dict]) -> None:
    """Add any language TMDB knows that the built-in table lacks (english_name -> code)."""
    for item in languages or []:
        code, name = item.get("iso_639_1"), (item.get("english_name") or "").strip()
        if not code or not name or name.casefold() == "no language":
            continue
        LANGUAGE_CODES.setdefault(name.casefold(), (code,))
        CODE_NAMES.setdefault(code, name)
