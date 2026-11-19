"""Build supports before their children without changing authored scene data."""


# 按支撑依赖排序对象，保证支撑者先于子对象并拒绝缺失父对象或环。
def objects_in_support_order(objects):
    by_id = {obj['id']: obj for obj in objects}
    ordered, complete, visiting = [], set(), set()

    # 深度优先访问支撑链，用访问栈检测环并追加拓扑顺序。
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


# 一次遍历收集全部缺失支撑父对象和互不相连的支撑环问题。
def support_reference_issues(objects):
    """Find every missing parent and disjoint cycle in one graph traversal."""
    records = [(index, obj) for index, obj in enumerate(objects)
               if isinstance(obj, dict) and isinstance(obj.get('id'), str)]
    by_id = {obj['id']: obj for _, obj in records}
    indexes = {obj['id']: index for index, obj in records}
    issues = []

    # 为支撑图问题记录字段路径、受影响对象和阻断原因。
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
