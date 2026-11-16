"""Build supports before their children without changing authored scene data."""


def objects_in_support_order(objects):
    by_id = {obj['id']: obj for obj in objects}
    ordered, complete, visiting = [], set(), set()

    def visit(obj):
        key = obj['id']
        if key in complete:
            return
        if key in visiting:
            raise ValueError(f'cyclic support reference at object {key}')
        visiting.add(key)
        parent = obj['support']
        if parent != 'ground':
            if parent not in by_id:
                raise ValueError(f'object {key}: support {parent!r} does not exist')
            visit(by_id[parent])
        visiting.remove(key)
        complete.add(key)
        ordered.append(obj)

    for obj in objects:
        visit(obj)
    return ordered


def support_reference_issues(objects):
    """Find every missing parent and disjoint cycle in one graph traversal."""
    records = [(index, obj) for index, obj in enumerate(objects)
               if isinstance(obj, dict) and isinstance(obj.get('id'), str)]
    by_id = {obj['id']: obj for _, obj in records}
    indexes = {obj['id']: index for index, obj in records}
    issues = []

    def issue(code, ids, message):
        issues.append({'code': code, 'path': f'/objects/{indexes[ids[0]]}/support',
                       'object_ids': ids, 'severity': 'error', 'blocking': True,
                       'basis': 'declared_support_graph', 'message': message})

    for _, obj in records:
        parent = obj.get('support')
        if isinstance(parent, str) and parent != 'ground' and parent not in by_id:
            issue('missing_support', [obj['id']],
                  f"object {obj['id']}: support {parent!r} does not exist")
    complete = set()
    for _, obj in records:
        chain, visiting, key = [], {}, obj['id']
        while key in by_id and key not in complete:
            if key in visiting:
                cycle = chain[visiting[key]:]
                issue('cyclic_support', cycle,
                      'cyclic support reference: ' + ' -> '.join([*cycle, key]))
                break
            visiting[key] = len(chain)
            chain.append(key)
            parent = by_id[key].get('support')
            if not isinstance(parent, str) or parent == 'ground':
                break
            key = parent
        complete.update(chain)
    return issues
