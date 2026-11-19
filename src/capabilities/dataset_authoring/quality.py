"""Validate the public answer contract without calling a model."""


# 检查选择题答案确实存在于公开选项的显式标识中。
def validate_choice(template, answer, presentation):
    if template.get('output_kind') != 'choice':
        return
    choices = presentation.get('options', [])
    identifiers = set()
    for option in choices:
        # A bare ID or an explicit `ID: label` / `ID：label` is unambiguous.
        identifier = option.split('：', 1)[0].split(':', 1)[0].strip()
        identifiers.add(identifier)
    selected = answer.get('choice_id')
    selected = selected if isinstance(selected, list) else [selected]
    if not selected or not identifiers or any(value not in identifiers for value in selected):
        raise ValueError('Choice answer is absent from public option IDs; use explicit ID: label options')
