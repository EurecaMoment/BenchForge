"""Resolve directed support paths and report the evidence for each native hop."""

from capabilities.scene_authoring.geometry.relations import supported_by_native_parts, supported_by_parts


def support_path(objects, source, target):
    """Follow declared support without crossing a mount or merging branches at ground."""
    path, seen = [source], {source}
    while path[-1] in objects:
        obj = objects[path[-1]]
        metadata = obj.get('metadata')
        if isinstance(metadata, dict) and metadata.get('spatial_attachment'):
            break
        parent = obj.get('support')
        if not parent or parent in seen:
            break
        path.append(parent)
        if parent == target or parent == 'ground':
            break
        seen.add(parent)
    return path


def relation_requires_measurement(relation):
    return relation.get('requires_measurement') is True or any(
        key.endswith(('_distance_m', '_clearance_m', '_tolerance_m', '_gap_m'))
        or key in {'distance_m', 'clearance_m', 'tolerance_m', 'gap_m'}
        for key in relation
    )


def native_support(child, parent, boxes, entities, executed_parts):
    """Distinguish native top-face evidence, insufficient bounds, and a measured gap."""
    if child not in boxes or (parent != 'ground' and parent not in boxes):
        return None, 'native support evidence is missing'
    lo, hi = boxes[child]
    if parent == 'ground':
        if abs(lo[2]) <= .04:
            return True, 'native bounds at nominal ground'
        return None, 'native bounds differ from nominal ground Z=0; check the actual floor and visible contact'
    if (supported_by_native_parts((lo, hi), entities[parent]) if executed_parts.get(parent)
            else supported_by_parts((lo, hi), [boxes[parent]])):
        return True, 'native bounds match a support surface'
    other_lo, other_hi = boxes[parent]
    if any(max(lo[i], other_lo[i]) - min(hi[i], other_hi[i]) > .04 for i in range(3)):
        return False, 'a measured gap separates the object from its declared support'
    return None, 'native bounds do not verify this support; inspect actual contact in the captured views'


def support_check(objects, source, target, states):
    """Confirm a transitive relation only when the target and each measured hop agree."""
    path = support_path(objects, source, target)
    hops = [{'source': child, 'target': parent, 'passed': states.get((child, parent))}
            for child, parent in zip(path, path[1:])]
    reaches_target = len(path) > 1 and path[-1] == target
    if any(hop['passed'] is False for hop in hops):
        return False, hops
    if reaches_target and all(hop['passed'] is True for hop in hops):
        return True, hops
    return None, hops


def visual_relation_indices(brief, native_checks):
    """Use the same visual relation coverage in the prompt and the acceptance gate."""
    unconfirmed = {row['index'] for row in native_checks if row.get('visual_check_required')}
    return {i for i, row in enumerate(brief['relations'])
            if row['type'] not in {'supported_by', 'mounted_to'} or i in unconfirmed}
