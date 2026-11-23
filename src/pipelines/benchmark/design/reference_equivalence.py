"""Review equivalence, not semantic inference: never rewrite a stored scene design."""
import re

# Only the declarative wrapper varies. Subject and complete predicate must match;
# unknown prose, qualifiers, object names, negations and coordinate signs stay literal.
_EN = re.compile(r'(front|back|left|right|above|below|up|down) (?:means|is defined as|is the direction) ([^.!?;:\n]+)\.?', re.ASCII | re.IGNORECASE)
_ZH = re.compile(r'(前方|后方|左方|右方|上方|下方)(?:是指|指的是|定义为)([^。！？；：\n]+)。?')


def _definition(text):
    for lang, expr in (('en', _EN), ('zh', _ZH)):
        match = expr.fullmatch(text.strip())
        if match:
            return lang, match[1].lower(), match[2].strip()
    return None


def align_reference(before, after, mapping):
    """Align typed references on the comparison copy, within one design only."""
    left, right = before.get('spatial_reference'), after.get('spatial_reference')
    if isinstance(left, str) and isinstance(right, str):
        key = _definition(left)
        if key is not None and key == _definition(right):
            before['spatial_reference'] = right
        return
    if not isinstance(left, dict) or left.get('schema') != 'benchforge.review-reference/v1':
        return
    for frame in left.get('frames', ()):
        if not isinstance(frame, dict):
            continue
        keys = {'world': ('origin_object_id',), 'user': ('origin_object_id', 'observer_object_id', 'facing_target_object_id')}.get(frame.get('kind'), ())
        for key in keys:
            if isinstance(frame.get(key), str) and frame[key] in mapping:
                frame[key] = mapping[frame[key]]
