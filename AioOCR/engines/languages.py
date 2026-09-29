# coding=utf-8
"""
Languages shared by every AioOCR engine.

Callers always speak **canonical codes**: ``auto``, then BCP-47 style language tags: ISO 639-1 where one exists
(``en``, ``ar``, ``fr``, ``zh``), ISO 639-3 otherwise (``abq``, ``bho``), plus the two script variants ``zh-Hant``
(Traditional Chinese) and ``sr-Latn`` (Serbian, Latin script). ``normalize`` turns anything a user or another tool
may send (``eng``, ``English``, ``fre``, ``chi_sim``, ``ch_sim``, ``en-US``, ``zh-TW``, ``"en+ar"``...) into canonical
codes; each engine then translates canonical codes into its own format (``toEngineLanguage``).
"""
from __future__ import absolute_import, division, print_function

AUTO = 'auto'

# code | English name | ISO 639-2/T or 639-3 | Tesseract traineddata ('' when Tesseract has none)
_TABLE = """
af|Afrikaans|afr|afr
am|Amharic|amh|amh
ar|Arabic|ara|ara
as|Assamese|asm|asm
av|Avar|ava|
az|Azerbaijani|aze|aze
ba|Bashkir|bak|
be|Belarusian|bel|bel
bg|Bulgarian|bul|bul
bh|Bihari|bih|
bn|Bengali|ben|ben
bo|Tibetan|bod|bod
br|Breton|bre|bre
bs|Bosnian|bos|bos
ca|Catalan|cat|cat
ce|Chechen|che|
cs|Czech|ces|ces
cv|Chuvash|chv|
cy|Welsh|cym|cym
da|Danish|dan|dan
de|German|deu|deu
el|Greek|ell|ell
en|English|eng|eng
eo|Esperanto|epo|epo
es|Spanish|spa|spa
et|Estonian|est|est
eu|Basque|eus|eus
fa|Persian|fas|fas
fi|Finnish|fin|fin
fo|Faroese|fao|fao
fr|French|fra|fra
fy|Western Frisian|fry|fry
ga|Irish|gle|gle
gd|Scottish Gaelic|gla|gla
gl|Galician|glg|glg
gu|Gujarati|guj|guj
ha|Hausa|hau|
he|Hebrew|heb|heb
hi|Hindi|hin|hin
hr|Croatian|hrv|hrv
ht|Haitian Creole|hat|hat
hu|Hungarian|hun|hun
hy|Armenian|hye|hye
id|Indonesian|ind|ind
is|Icelandic|isl|isl
it|Italian|ita|ita
iu|Inuktitut|iku|iku
ja|Japanese|jpn|jpn
jv|Javanese|jav|jav
ka|Georgian|kat|kat
kk|Kazakh|kaz|kaz
km|Khmer|khm|khm
kn|Kannada|kan|kan
ko|Korean|kor|kor
ku|Kurdish|kur|kmr
kv|Komi|kom|
ky|Kyrgyz|kir|kir
la|Latin|lat|lat
lb|Luxembourgish|ltz|ltz
lo|Lao|lao|lao
lt|Lithuanian|lit|lit
lv|Latvian|lav|lav
mg|Malagasy|mlg|
mi|Maori|mri|mri
mk|Macedonian|mkd|mkd
ml|Malayalam|mal|mal
mn|Mongolian|mon|mon
mo|Moldavian|mol|
mr|Marathi|mar|mar
ms|Malay|msa|msa
mt|Maltese|mlt|mlt
my|Burmese|mya|mya
ne|Nepali|nep|nep
nl|Dutch|nld|nld
no|Norwegian|nor|nor
oc|Occitan|oci|oci
om|Oromo|orm|
or|Odia|ori|ori
os|Ossetian|oss|
pa|Punjabi|pan|pan
pi|Pali|pli|
pl|Polish|pol|pol
ps|Pashto|pus|pus
pt|Portuguese|por|por
qu|Quechua|que|que
rm|Romansh|roh|
ro|Romanian|ron|ron
ru|Russian|rus|rus
sa|Sanskrit|san|san
sd|Sindhi|snd|snd
si|Sinhala|sin|sin
sk|Slovak|slk|slk
sl|Slovenian|slv|slv
so|Somali|som|
sq|Albanian|sqi|sqi
sr|Serbian (Cyrillic)|srp|srp
sr-Latn|Serbian (Latin)|srp|srp_latn
su|Sundanese|sun|sun
sv|Swedish|swe|swe
sw|Swahili|swa|swa
ta|Tamil|tam|tam
te|Telugu|tel|tel
tg|Tajik|tgk|tgk
th|Thai|tha|tha
ti|Tigrinya|tir|tir
tl|Tagalog|tgl|tgl
tr|Turkish|tur|tur
tt|Tatar|tat|tat
ug|Uyghur|uig|uig
uk|Ukrainian|ukr|ukr
ur|Urdu|urd|urd
uz|Uzbek|uzb|uzb
vi|Vietnamese|vie|vie
xh|Xhosa|xho|
yi|Yiddish|yid|yid
yo|Yoruba|yor|yor
zh|Chinese (Simplified)|zho|chi_sim
zh-Hant|Chinese (Traditional)|zho|chi_tra
abq|Abaza|abq|
ady|Adyghe|ady|
ang|Old English|ang|
bgc|Haryanvi|bgc|
bho|Bhojpuri|bho|
bua|Buryat|bua|
dar|Dargwa|dar|
gom|Konkani (Goan)|gom|
inh|Ingush|inh|
kaa|Karakalpak|kaa|
kbd|Kabardian|kbd|
lbe|Lak|lbe|
lez|Lezghian|lez|
mah|Magahi|mag|
mai|Maithili|mai|
mhr|Meadow Mari|mhr|
mni|Manipuri|mni|
new|Newari|new|
sah|Yakut|sah|
sck|Sadri|sck|
tab|Tabassaran|tab|
tyv|Tuvan|tyv|
udm|Udmurt|udm|
xal|Kalmyk|xal|
"""

LANGUAGES = {}      # canonical code -> {'code', 'name', 'iso3', 'tesseract'}
for _line in _TABLE.strip().splitlines():
    _code, _name, _iso3, _tess = _line.split('|')
    LANGUAGES[_code] = {'code': _code, 'name': _name, 'iso3': _iso3, 'tesseract': _tess}

# Other spellings -> canonical code. Built from the table, then the aliases real tools use.
_ALIASES = {}
for _code, _l in LANGUAGES.items():
    _ALIASES.setdefault(_code.lower(), _code)
    _ALIASES.setdefault(_l['name'].lower(), _code)
    _ALIASES.setdefault(_l['name'].split(' (')[0].lower(), _code)
    if _l['tesseract']:
        _ALIASES.setdefault(_l['tesseract'].lower(), _code)
for _code, _l in LANGUAGES.items():  # ISO 639-2/T last: "srp" / "zho" must not steal the script variants
    if _code not in ('sr-Latn', 'zh-Hant'):
        _ALIASES.setdefault(_l['iso3'], _code)
_ALIASES.update({
    # ISO 639-2/B (and OCR.Space) codes
    'fre': 'fr', 'ger': 'de', 'dut': 'nl', 'cze': 'cs', 'gre': 'el', 'chi': 'zh', 'per': 'fa', 'rum': 'ro',
    'slo': 'sk', 'alb': 'sq', 'arm': 'hy', 'baq': 'eu', 'bur': 'my', 'geo': 'ka', 'ice': 'is', 'mac': 'mk',
    'may': 'ms', 'mao': 'mi', 'tib': 'bo', 'wel': 'cy', 'chs': 'zh', 'cht': 'zh-Hant',
    # BCP-47 variants
    'zh-cn': 'zh', 'zh-hans': 'zh', 'zh-sg': 'zh', 'zh-tw': 'zh-Hant', 'zh-hk': 'zh-Hant', 'zh-mo': 'zh-Hant',
    'zh-hant': 'zh-Hant', 'sr-latn': 'sr-Latn', 'sr-cyrl': 'sr', 'iw': 'he', 'in': 'id', 'ji': 'yi', 'fil': 'tl',
    'nb': 'no', 'nn': 'no', 'filipino': 'tl', 'farsi': 'fa', 'chinese': 'zh', 'serbian': 'sr',
    # EasyOCR / PaddleOCR / Tesseract specific names
    'ch_sim': 'zh', 'ch_tra': 'zh-Hant', 'ch': 'zh', 'chinese_cht': 'zh-Hant', 'rs_cyrillic': 'sr',
    'rs_latin': 'sr-Latn', 'japan': 'ja', 'korean': 'ko', 'german': 'de', 'french': 'fr', 'tjk': 'tg',
    'che': 'ce', 'ava': 'av', 'srp_latn': 'sr-Latn', 'chi_sim': 'zh', 'chi_tra': 'zh-Hant', 'kmr': 'ku',
    'aze_cyrl': 'az', 'uzb_cyrl': 'uz',
})


def normalize(value, keepUnknown=True):
    """
    :param value: str | list  e.g. ``'en'``, ``'eng'``, ``'English'``, ``'en+ar'``, ``'en, fr'``, ``['ar', 'auto']``
    :param keepUnknown: bool  keep tokens that are not in the table (lower-cased) instead of dropping them
    :return: list[str]  canonical codes, in order, without duplicates (``[]`` for an empty value)
    """
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    tokens = []
    for item in items:
        for part in str(item).replace('+', ',').replace(';', ',').replace(' ', ',').split(','):
            part = part.strip()
            if part:
                tokens.append(part)
    out = []
    for token in tokens:
        low = token.lower()
        code = AUTO if low in (AUTO, 'automatic', 'detect') else _ALIASES.get(low)
        if code is None and '-' in low:  # en-US, pt-BR, ar-TN -> the language itself
            code = _ALIASES.get(low.split('-')[0])
        if code is None and '_' in low:  # en_US
            code = _ALIASES.get(low.split('_')[0])
        if code is None:
            if not keepUnknown:
                continue
            code = low
        if code not in out:
            out.append(code)
    return out


def name(code):
    """:return: str  English name of a canonical code ('Automatic' for auto, the code itself when unknown)"""
    if code == AUTO:
        return 'Automatic (detect the language)'
    entry = LANGUAGES.get(code)
    return entry['name'] if entry else code


def catalog(codes=None):
    """
    :param codes: iterable[str] | None  canonical codes, ``None`` for every known language
    :return: list[dict]  ``{'code', 'name'}``, ``auto`` first, then sorted by name
    """
    codes = list(LANGUAGES) if codes is None else list(codes)
    head = [{'code': AUTO, 'name': name(AUTO)}] if AUTO in codes else []
    rest = sorted(({'code': c, 'name': name(c)} for c in codes if c != AUTO), key=lambda x: x['name'].lower())
    return head + rest


def fromEngineCodes(mapping):
    """
    :param mapping: iterable[str]  an engine's own language codes
    :return: dict  canonical code -> the engine's code (codes that do not normalise to a known language are kept as is)
    """
    out = {}
    for native in mapping:
        canon = normalize(native)
        if canon:
            out.setdefault(canon[0], native)
    return out


__all__ = ['AUTO', 'LANGUAGES', 'catalog', 'fromEngineCodes', 'name', 'normalize']
