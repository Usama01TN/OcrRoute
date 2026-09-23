# coding=utf-8
"""Ensemble reconciliation: cluster word boxes across engine results by IoU and vote on text."""
from __future__ import absolute_import, division, print_function

from collections import Counter

from ocrroute.enginelib import OCRPlugin


def _iou(a, b):
    ax1, ay1, ax2, ay2 = a['Left'], a['Top'], a['Left'] + a['Width'], a['Top'] + a['Height']
    bx1, by1, bx2, by2 = b['Left'], b['Top'], b['Left'] + b['Width'], b['Top'] + b['Height']
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = a['Width'] * a['Height'] + b['Width'] * b['Height'] - inter
    return inter / union if union > 0 else 0.0


def reconcile(results, iou_threshold=0.4):
    """
    :param results: list of (engine_id, quality_score, unified_result) from successful attempts.
    :return: (consensus unified result, votes detail)
    """
    good = [(e, q, r) for e, q, r in results if r.get('FileParseExitCode') != -1]
    if not good:
        return OCRPlugin.emptyResult(), {'engines': [], 'clusters': 0}
    if len(good) == 1:
        return good[0][2], {'engines': [good[0][0]], 'clusters': 0, 'single': True}
    words_by_engine = []
    for eng, q, res in good:
        words = [w for line in res.get('TextOverlay', {}).get('Lines', []) for w in line.get('Words', [])]
        words_by_engine.append((eng, q, words))
    # engines without geometry cannot vote geometrically; fall back to the best-quality full text
    if any(not ws for _, _, ws in words_by_engine):
        best = max(good, key=lambda t: (t[1], len(t[2].get('ParsedText', ''))))
        return best[2], {'engines': [e for e, _, _ in good], 'clusters': 0, 'fallback': best[0]}
    clusters = []
    for eng, q, words in words_by_engine:
        for w in words:
            placed = False
            for cl in clusters:
                if any(_iou(w, other) >= iou_threshold for _, _, other in cl):
                    cl.append((eng, q, w))
                    placed = True
                    break
            if not placed:
                clusters.append([(eng, q, w)])
    out_words = []
    disagreements = 0
    for cl in clusters:
        texts = Counter(w['WordText'] for _, _, w in cl)
        top_text, top_n = texts.most_common(1)[0]
        if len(texts) > 1:
            disagreements += 1
            if list(texts.values()).count(top_n) > 1:  # tie → higher quality engine wins
                top_text = max(cl, key=lambda t: t[1])[2]['WordText']
        # geometry: average of contributing boxes for the winning text
        boxes = [w for _, _, w in cl if w['WordText'] == top_text] or [cl[0][2]]
        n = len(boxes)
        out_words.append(
            OCRPlugin.makeWord(
                top_text,
                sum(b['Left'] for b in boxes) / n,
                sum(b['Top'] for b in boxes) / n,
                sum(b['Width'] for b in boxes) / n,
                sum(b['Height'] for b in boxes) / n,
            )
        )
    result = OCRPlugin().buildResult(out_words)
    return result, {'engines': [e for e, _, _ in good], 'clusters': len(clusters), 'disagreements': disagreements}
