# coding=utf-8
"""Result post-processing: normalisation, confidence filtering, page stitching, RTL, the Tools no-op hook."""
from __future__ import absolute_import, division, print_function

import re
import unicodedata

from ocrroute.enginelib import OCRPlugin

_RTL_RANGES = ((0x0590, 0x08FF), (0xFB1D, 0xFDFF), (0xFE70, 0xFEFF))


def isRtlText(text):
    rtl = sum(1 for ch in text if any(a <= ord(ch) <= b for a, b in _RTL_RANGES))
    letters = sum(1 for ch in text if ch.isalpha())
    return letters > 0 and rtl / letters > 0.5


def normaliseWhitespace(result):
    for line in result.get('TextOverlay', {}).get('Lines', []):
        for w in line.get('Words', []):
            w['WordText'] = unicodedata.normalize('NFC', re.sub(r'\s+', ' ', str(w['WordText']))).strip()
        line['LineText'] = ' '.join(w['WordText'] for w in line.get('Words', []) if w['WordText']).strip() or line.get(
            'LineText', ''
        )
    lines = [ln['LineText'] for ln in result.get('TextOverlay', {}).get('Lines', [])]
    if lines:
        result['ParsedText'] = '\r\n'.join(lines)
    return result


def applyRtlOrder(result):
    """Words are stored left-to-right geometrically; for RTL scripts the reading order is right-to-left."""
    for line in result.get('TextOverlay', {}).get('Lines', []):
        words = line.get('Words', [])
        if words and isRtlText(' '.join(w['WordText'] for w in words)):
            words.sort(key=lambda w: -(w['Left'] + w['Width']))
            line['LineText'] = ' '.join(w['WordText'] for w in words)
    result['ParsedText'] = '\r\n'.join(ln['LineText'] for ln in result.get('TextOverlay', {}).get('Lines', []))
    return result


def stitchPages(page_results, page_indices):
    """Merge per-page unified results into one, tagging every line with its page number (1-based)."""
    if len(page_results) == 1:
        res = page_results[0]
        for line in res.get('TextOverlay', {}).get('Lines', []):
            line.setdefault('Page', page_indices[0] + 1 if page_indices else 1)
        return res
    merged = OCRPlugin.emptyResult()
    texts = []
    for res, idx in zip(page_results, page_indices):
        if res.get('FileParseExitCode') == -1:
            continue
        for line in res.get('TextOverlay', {}).get('Lines', []):
            line['Page'] = idx + 1
            merged['TextOverlay']['Lines'].append(line)
        if res.get('ParsedText'):
            texts.append(res['ParsedText'])
    merged['ParsedText'] = '\r\n\f\r\n'.join(texts)
    merged['TextOverlay']['Message'] = 'Total lines: {}'.format(len(merged['TextOverlay']['Lines']))
    merged['TextOverlay']['HasOverlay'] = bool(merged['TextOverlay']['Lines'])
    merged['Pages'] = len(page_results)
    return merged


def stats(result):
    lines = result.get('TextOverlay', {}).get('Lines', [])
    words = [w for ln in lines for w in ln.get('Words', [])]
    confs = [float(w['Confidence']) for w in words if isinstance(w, dict) and 'Confidence' in w]
    return {
        'chars': len(result.get('ParsedText', '')),
        'lines': len(lines),
        'words': len(words),
        'mean_confidence': round(sum(confs) / len(confs), 4) if confs else 0.0,
    }


def applyTools(result, tool_chain):
    """Reserved extension point (see ocrroute/tools). With an empty chain - always, today - this is identity."""
    if not tool_chain:
        return result
    from ocrroute.tools.registry import getToolRegistry

    reg = getToolRegistry()
    for step in tool_chain:  # pragma: no cover - no tools exist yet
        tool = reg.get(step['name'] if isinstance(step, dict) else str(step))
        if tool is None:
            raise LookupError("Tool '{}' is not installed".format(step))
        result = tool.run(result, **(step.get('options', {}) if isinstance(step, dict) else {}))
    return result
